from datetime import date, datetime, timezone

import pytest
from centaurodb.engine import Engine
from centaurodb.collection import Collection
from centaurodb.async_collection import AsyncCollection
from centaurodb.model import CentauroModel
from centaurodb.query import Field

from conftest import Sensor


class Book(CentauroModel):
    __centauro_name__ = "Book"
    title: str = ""
    year: int = 0
    country: str = ""
    published: date | None = None
    released_at: datetime | None = None


@pytest.fixture
def col():
    with Engine(":memory:") as engine:
        col = Collection(engine, "sensors")
        sensors = [
            Sensor(status="active", level=10, location="roof"),
            Sensor(status="active", level=3, location="basement"),
            Sensor(status="inactive", level=7, location="garage"),
            Sensor(status="active", level=5, location="garden"),
        ]
        for s in sensors:
            col.write_object(s)
        yield col


@pytest.fixture
def book_col():
    with Engine(":memory:") as engine:
        col = Collection(engine, "books")
        books = [
            Book(
                title="A",
                year=1999,
                country="Germany",
                published=date(1999, 5, 1),
                released_at=datetime(1999, 5, 1, tzinfo=timezone.utc),
            ),
            Book(
                title="B",
                year=2005,
                country="Germany",
                published=date(2005, 3, 10),
                released_at=datetime(2005, 3, 10, 12, 0, tzinfo=timezone.utc),
            ),
            Book(
                title="C",
                year=2005,
                country="Germany",
                published=date(2005, 8, 20),
                released_at=datetime(2005, 8, 20, 18, 30, tzinfo=timezone.utc),
            ),
            Book(
                title="D",
                year=2010,
                country="Italy",
                published=date(2010, 1, 1),
                released_at=datetime(2010, 1, 1, tzinfo=timezone.utc),
            ),
        ]
        for b in books:
            col.write_object(b)
        yield col


# --- Model class shorthand ---


def test_read_objects_model_class_returns_all(col):
    results = col.read_objects(Sensor)
    assert len(results) == 4
    assert all(isinstance(r, Sensor) for r in results)


def test_read_objects_model_class_empty_collection():
    with Engine(":memory:") as engine:
        col = Collection(engine, "sensors")
        results = col.read_objects(Sensor)
        assert results == []


def test_read_objects_model_class_type_isolation(col):
    class Camera(CentauroModel):
        __centauro_name__ = "Camera"
        status: str = "off"

    col.write_object(Camera(status="on"))

    # Sensor class should only return Sensors
    results = col.read_objects(Sensor)
    assert len(results) == 4
    assert all(isinstance(r, Sensor) for r in results)

    # Camera class should only return Cameras
    results = col.read_objects(Camera)
    assert len(results) == 1
    assert isinstance(results[0], Camera)


def test_read_objects_model_class_with_extra_args_raises(col):
    with pytest.raises(TypeError, match="no additional arguments"):
        col.read_objects(Sensor, Sensor.fields.status == "active")


def test_read_objects_model_class_raw(col):
    results = col.read_objects(Sensor, raw=True)
    assert len(results) == 4
    assert "meta" in results[0].keys()
    assert not isinstance(results[0], Sensor)


def test_read_objects_model_class_persisted_flags(col):
    results = col.read_objects(Sensor)
    for r in results:
        assert r._persisted is True
        assert r.row is not None
        assert r.row.write_time is not None


# --- Operators ---


def test_equality(col):
    results = col.read_objects(Sensor.fields.status == "active")
    assert len(results) == 3


def test_not_equal(col):
    results = col.read_objects(Sensor.fields.status != "active")
    assert len(results) == 1
    assert results[0].status == "inactive"


def test_greater_than(col):
    results = col.read_objects(Sensor.fields.level > 5)
    assert len(results) == 2
    assert {s.level for s in results} == {10, 7}


def test_greater_than_or_equal(col):
    results = col.read_objects(Sensor.fields.level >= 7)
    assert len(results) == 2


def test_less_than(col):
    results = col.read_objects(Sensor.fields.level < 5)
    assert len(results) == 1
    assert results[0].level == 3


def test_less_than_or_equal(col):
    results = col.read_objects(Sensor.fields.level <= 5)
    assert len(results) == 2


def test_like(col):
    results = col.read_objects(Sensor.fields.location.like("gar%"))
    assert len(results) == 2
    assert {s.location for s in results} == {"garage", "garden"}


def test_is_in(col):
    results = col.read_objects(
        Sensor.fields.location.is_in(["roof", "basement"]),
    )
    assert len(results) == 2
    assert {s.location for s in results} == {"roof", "basement"}


def test_is_in_empty_list_raises(col):
    with pytest.raises(ValueError, match="non-empty list"):
        Sensor.fields.location.is_in([])


def test_is_in_exceeds_max_size_raises():
    with pytest.raises(ValueError, match="5000 element limit"):
        Sensor.fields.location.is_in(list(range(5001)))


# --- Multiple conditions ---


def test_multiple_conditions_and(col):
    results = col.read_objects(
        Sensor.fields.status == "active",
        Sensor.fields.level > 5,
    )
    assert len(results) == 1
    assert results[0].level == 10


# --- Edge cases ---


def test_empty_result_set(col):
    results = col.read_objects(Sensor.fields.level > 100)
    assert results == []


def test_no_conditions_returns_all(col):
    results = col.read_objects(Sensor.fields)
    assert len(results) == 4


# --- Validation ---


def test_raw_field_without_model_raises_error(col):
    with pytest.raises(ValueError, match="Cannot infer model class"):
        col.read_objects(Field("status") == "active")


def test_no_args_raises_type_error(col):
    with pytest.raises(TypeError):
        col.read_objects()


def test_mixed_model_conditions_raise_error(col):
    class Station(CentauroModel):
        __centauro_name__ = "Station"
        status: str = "inactive"

    with pytest.raises(ValueError, match="same model class"):
        col.read_objects(
            Sensor.fields.level > 5,
            Station.fields.status == "active",
        )


# --- Raw mode ---


def test_raw_returns_rows(col):
    results = col.read_objects(Sensor.fields.status == "active", raw=True)
    assert len(results) == 3
    assert "meta" in results[0].keys()
    assert not isinstance(results[0], Sensor)


# --- Standard columns ---


def test_filter_by_id(col):
    s = Sensor(status="special", level=99)
    col.write_object(s)
    results = col.read_objects(Sensor.fields.id == s.row.id)
    assert len(results) == 1
    assert results[0].level == 99


# --- Row metadata ---


def test_persisted_flag_and_row_fields(col):
    results = col.read_objects(Sensor.fields)
    for r in results:
        assert r._persisted is True
        assert r.row is not None
        assert r.row.write_time is not None


# --- Type isolation ---


def test_type_isolation_filters_by_centauro_name(col):
    class Camera(CentauroModel):
        __centauro_name__ = "Camera"
        status: str = "off"

    col.write_object(Camera(status="active"))

    # Should only return Sensors, not Cameras
    results = col.read_objects(Sensor.fields.status == "active")
    assert len(results) == 3
    assert all(isinstance(r, Sensor) for r in results)

    # Camera query should return only the Camera
    results = col.read_objects(Camera.fields)
    assert len(results) == 1
    assert isinstance(results[0], Camera)


def test_type_isolation_same_field_value(col):
    class Device(CentauroModel):
        __centauro_name__ = "Device"
        status: str = "inactive"
        location: str = ""

    col.write_object(Device(status="active", location="roof"))

    results = col.read_objects(Sensor.fields.location == "roof")
    assert len(results) == 1
    assert isinstance(results[0], Sensor)


# --- OR conditions ---


def test_or_two_values(col):
    results = col.read_objects(
        (Sensor.fields.status == "active") | (Sensor.fields.status == "inactive"),
    )
    assert len(results) == 4


def test_or_subset_of_values(col):
    results = col.read_objects(
        (Sensor.fields.status == "active") | (Sensor.fields.status == "pending"),
    )
    assert len(results) == 3


def test_max_field_with_condition(book_col):
    results = book_col.read_objects(
        Book,
        Book.fields.country == "Germany",
        max_field=Book.fields.year,
    )
    assert {b.title for b in results} == {"B", "C"}


def test_min_field_with_condition(book_col):
    results = book_col.read_objects(
        Book,
        Book.fields.country == "Germany",
        min_field=Book.fields.year,
    )
    assert {b.title for b in results} == {"A"}


def test_max_field_date(book_col):
    results = book_col.read_objects(
        Book.fields.country == "Germany",
        max_field=Book.fields.published,
    )
    assert {b.title for b in results} == {"C"}


def test_max_field_datetime(book_col):
    results = book_col.read_objects(
        Book.fields.country == "Germany",
        max_field=Book.fields.released_at,
    )
    assert {b.title for b in results} == {"C"}


def test_max_min_mutual_exclusion(book_col):
    with pytest.raises(TypeError, match="only one of max_field or min_field"):
        book_col.read_objects(
            Book,
            Book.fields.country == "Germany",
            max_field=Book.fields.year,
            min_field=Book.fields.year,
        )


def test_max_field_rejects_non_numeric(book_col):
    with pytest.raises(TypeError, match="int, float, date, or datetime"):
        book_col.read_objects(Book, max_field=Book.fields.title)


def test_max_field_requires_model_bound_field(book_col):
    with pytest.raises(ValueError, match="must be bound to a model class"):
        book_col.read_objects(Book.fields.country == "Germany", max_field=Field("year"))


def test_or_with_and(col):
    # (status==active OR status==inactive) AND level > 5
    results = col.read_objects(
        (Sensor.fields.status == "active") | (Sensor.fields.status == "inactive"),
        Sensor.fields.level > 5,
    )
    assert len(results) == 2
    assert {s.level for s in results} == {10, 7}


def test_or_three_way(col):
    results = col.read_objects(
        (Sensor.fields.location == "roof")
        | (Sensor.fields.location == "basement")
        | (Sensor.fields.location == "garage"),
    )
    assert len(results) == 3
    assert {s.location for s in results} == {"roof", "basement", "garage"}


def test_or_cross_model_raises():
    with Engine(":memory:") as engine:
        col = Collection(engine, "sensors")

        class Station(CentauroModel):
            __centauro_name__ = "Station"
            status: str = "inactive"

        with pytest.raises(ValueError, match="same model class"):
            col.read_objects(
                (Sensor.fields.status == "active")
                | (Station.fields.status == "inactive"),
            )


# --- Async ---


@pytest.mark.asyncio
async def test_async_read_objects():
    with Engine(check_same_thread=False) as engine:
        col = AsyncCollection(engine, "sensors")

        sensors = [
            Sensor(status="active", level=10, location="roof"),
            Sensor(status="inactive", level=3, location="basement"),
        ]
        for s in sensors:
            await col.write_object(s)

        results = await col.read_objects(Sensor.fields.status == "active")
        assert len(results) == 1
        assert results[0].level == 10

        # No conditions
        results = await col.read_objects(Sensor.fields)
        assert len(results) == 2
