"""PostgreSQL integration tests.

Skipped by default. Enable with: CENTAURODB_PG_URL=postgresql://user:pass@host/db
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pytest

from centaurodb import (
    CentauroModel,
    CentauroModelSeries,
    CentauroValues,
    Collection,
    Engine,
    TimeSeriesCollection,
    ObjectNotFoundError,
)
from centaurodb.engine import PostgresEngine

PG_URL = os.environ.get("CENTAURODB_PG_URL")
pytestmark = pytest.mark.skipif(PG_URL is None, reason="CENTAURODB_PG_URL not set")


class PgSensor(CentauroModel):
    __centauro_name__ = "PgSensor"
    unit: str = ""
    location: str = ""


class PgMetric(CentauroModelSeries):
    __centauro_name__ = "PgMetric"
    unit: str = ""


class PgMeasure(CentauroModel):
    __centauro_name__ = "PgMeasure"
    count: int = 0
    temperature: float = 0.0
    recorded_on: date = date(2025, 1, 1)
    recorded_at: datetime = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def pg_conn():
    conn = Engine(PG_URL)
    assert isinstance(conn, PostgresEngine)
    yield conn
    # Clean up test tables
    for table in [
        "pgtest_values",
        "pgtest_objects",
        "pgtimeseries_values",
        "pgtimeseries_objects",
        "pgmeasures_objects",
    ]:
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        except Exception:
            pass
    for view in ["pgtest_view_sensors", "pgtimeseries_view_metrics"]:
        try:
            conn.execute(f"DROP VIEW IF EXISTS {view}")
        except Exception:
            pass
    conn.commit()
    conn.close()


class TestPostgresCollection:
    def test_write_and_read(self, pg_conn):
        col = Collection(pg_conn, "pgtest")
        sensor = PgSensor(unit="celsius", location="lab")
        col.write_object(sensor)
        assert sensor._persisted
        assert sensor._row.id is not None

        loaded = col.read_object_by_id(sensor._row.id, PgSensor)
        assert loaded.unit == "celsius"
        assert loaded.location == "lab"

    def test_update(self, pg_conn):
        col = Collection(pg_conn, "pgtest")
        sensor = PgSensor(unit="celsius")
        col.write_object(sensor)
        sensor.unit = "fahrenheit"
        col.update_object(sensor)

        loaded = col.read_object_by_id(sensor._row.id, PgSensor)
        assert loaded.unit == "fahrenheit"

    def test_refresh(self, pg_conn):
        col = Collection(pg_conn, "pgtest")
        sensor = PgSensor(unit="celsius")
        col.write_object(sensor)

        # Modify directly in DB
        pg_conn.execute(
            "UPDATE pgtest_objects SET meta = %s::jsonb WHERE id = %s",
            ('{"unit": "kelvin", "location": ""}', sensor._row.id),
        )
        pg_conn.commit()

        col.refresh(sensor)
        assert sensor.unit == "kelvin"

    def test_not_found(self, pg_conn):
        col = Collection(pg_conn, "pgtest")
        with pytest.raises(ObjectNotFoundError):
            col.read_object_by_id(99999, PgSensor)

    def test_views_and_indexes(self, pg_conn):
        col = Collection(pg_conn, "pgtest")
        sensor = PgSensor(unit="celsius")
        col.write_object(sensor)

        col.create_view("sensors", PgSensor)
        views = col.describe_views()
        assert "pgtest_view_sensors" in views


class TestPostgresTimeSeries:
    def test_write_with_values(self, pg_conn):
        col = TimeSeriesCollection(pg_conn, "pgtimeseries")
        metric = PgMetric(
            unit="celsius",
            values=[
                CentauroValues(
                    time=datetime(2025, 1, 1, tzinfo=timezone.utc), value=20.5
                ),
                CentauroValues(
                    time=datetime(2025, 1, 2, tzinfo=timezone.utc), value=21.0
                ),
            ],
        )
        col.write_object(metric)
        assert metric._persisted

        loaded = col.read_object_by_id(metric._row.id, PgMetric)
        assert len(loaded.values) == 2
        assert loaded.values[0].value == 20.5

    def test_update_append(self, pg_conn):
        col = TimeSeriesCollection(pg_conn, "pgtimeseries")
        metric = PgMetric(
            unit="celsius",
            values=[
                CentauroValues(
                    time=datetime(2025, 1, 1, tzinfo=timezone.utc), value=20.5
                ),
            ],
        )
        col.write_object(metric)

        metric.values = [
            CentauroValues(time=datetime(2025, 1, 1, tzinfo=timezone.utc), value=99.0),
            CentauroValues(time=datetime(2025, 1, 2, tzinfo=timezone.utc), value=21.0),
        ]
        col.update_object(metric, values_mode="append")

        loaded = col.read_object_by_id(metric._row.id, PgMetric)
        assert len(loaded.values) == 2
        # Original value preserved (append ignores conflicts)
        assert loaded.values[0].value == 20.5
        assert loaded.values[1].value == 21.0

    def test_update_upsert(self, pg_conn):
        col = TimeSeriesCollection(pg_conn, "pgtimeseries")
        metric = PgMetric(
            unit="celsius",
            values=[
                CentauroValues(
                    time=datetime(2025, 1, 1, tzinfo=timezone.utc), value=20.5
                ),
            ],
        )
        col.write_object(metric)

        metric.values = [
            CentauroValues(time=datetime(2025, 1, 1, tzinfo=timezone.utc), value=99.0),
        ]
        col.update_object(metric, values_mode="upsert")

        loaded = col.read_object_by_id(metric._row.id, PgMetric)
        assert len(loaded.values) == 1
        assert loaded.values[0].value == 99.0

    def test_update_replace(self, pg_conn):
        col = TimeSeriesCollection(pg_conn, "pgtimeseries")
        metric = PgMetric(
            unit="celsius",
            values=[
                CentauroValues(
                    time=datetime(2025, 1, 1, tzinfo=timezone.utc), value=20.5
                ),
                CentauroValues(
                    time=datetime(2025, 1, 2, tzinfo=timezone.utc), value=21.0
                ),
            ],
        )
        col.write_object(metric)

        metric.values = [
            CentauroValues(time=datetime(2025, 1, 3, tzinfo=timezone.utc), value=22.0),
        ]
        col.update_object(metric, values_mode="replace")

        loaded = col.read_object_by_id(metric._row.id, PgMetric)
        assert len(loaded.values) == 1
        assert loaded.values[0].value == 22.0


class TestPostgresNumericAndAggregateCast:
    # Pins coverage on PostgresDialect._numeric_cast and _aggregate_cast.
    # The CRUD-only PG tests above don't exercise either, so without these
    # dialect.py lines 401 + 404-412 stay grey in CI even with the service
    # container running.

    def test_numeric_filter_on_jsonb_field(self, pg_conn):
        col = Collection(pg_conn, "pgmeasures")
        col.write_object(PgMeasure(count=1, temperature=10.0))
        col.write_object(PgMeasure(count=5, temperature=25.0))
        col.write_object(PgMeasure(count=10, temperature=50.0))

        results = col.read_objects(PgMeasure.fields.count > 3)
        assert {r.count for r in results} == {5, 10}

    def test_max_field_int(self, pg_conn):
        col = Collection(pg_conn, "pgmeasures")
        col.write_object(PgMeasure(count=1))
        col.write_object(PgMeasure(count=10))

        results = col.read_objects(PgMeasure, max_field=PgMeasure.fields.count)
        assert [r.count for r in results] == [10]

    def test_max_field_float(self, pg_conn):
        col = Collection(pg_conn, "pgmeasures")
        col.write_object(PgMeasure(temperature=1.5))
        col.write_object(PgMeasure(temperature=99.5))

        results = col.read_objects(PgMeasure, max_field=PgMeasure.fields.temperature)
        assert [r.temperature for r in results] == [99.5]

    def test_max_field_date(self, pg_conn):
        col = Collection(pg_conn, "pgmeasures")
        col.write_object(PgMeasure(recorded_on=date(2025, 1, 1)))
        col.write_object(PgMeasure(recorded_on=date(2025, 6, 1)))

        results = col.read_objects(PgMeasure, max_field=PgMeasure.fields.recorded_on)
        assert [r.recorded_on for r in results] == [date(2025, 6, 1)]

    def test_max_field_datetime(self, pg_conn):
        col = Collection(pg_conn, "pgmeasures")
        col.write_object(
            PgMeasure(recorded_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        )
        col.write_object(
            PgMeasure(recorded_at=datetime(2025, 12, 31, tzinfo=timezone.utc))
        )

        results = col.read_objects(PgMeasure, max_field=PgMeasure.fields.recorded_at)
        assert [r.recorded_at for r in results] == [
            datetime(2025, 12, 31, tzinfo=timezone.utc)
        ]
