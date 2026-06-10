from datetime import datetime

import pytest

from centaurodb import (
    Collection,
    ObjectNotFoundError,
    ObjectNotPersistedError,
    RowFields,
    TimeSeriesCollection,
    CentauroValues,
)

from conftest import SampleModel, SampleModelSeries, Sensor


class TestReadObject:
    def test_read_returns_pydantic_model(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))

        obj = col.read_object_by_id(1, SampleModel)
        assert isinstance(obj, SampleModel)
        assert obj.unit == "C"
        assert obj.location == "lab"

    def test_read_populates_row_fields(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))

        obj = col.read_object_by_id(1, SampleModel)
        assert isinstance(obj.row, RowFields)
        assert obj.row.id == 1
        assert obj.row.name == "SampleModel"
        assert isinstance(obj.row.write_time, datetime)
        assert isinstance(obj.row.edit_time, datetime)

    def test_row_is_none_before_read(self):
        obj = SampleModel(unit="C", location="lab")
        with pytest.raises(AttributeError, match="not been written to or read from"):
            obj.row
        assert obj._persisted is False

    def test_read_sets_persisted(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))
        obj = col.read_object_by_id(1, SampleModel)
        assert obj._persisted is True

    def test_read_raw_returns_row(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))

        row = col.read_object_by_id(1, SampleModel, raw=True)
        assert row["name"] == "SampleModel"

    def test_read_missing_raises(self, conn):
        col = Collection(conn, "items")
        with pytest.raises(ObjectNotFoundError, match="not found"):
            col.read_object_by_id(999, SampleModel)

    def test_read_wrong_model_class_raises(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))

        with pytest.raises(ObjectNotFoundError, match="Sensor"):
            col.read_object_by_id(1, Sensor)

    def test_read_wrong_model_class_timeseries_raises(self, conn):
        col = TimeSeriesCollection(conn, "ts")
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T00:00:00", value=1.0)],
            )
        )

        with pytest.raises(ObjectNotFoundError):
            col.read_object_by_id(1, Sensor)


class TestRefresh:
    def test_refresh_updates_meta(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        col.write_object(obj)

        conn.execute(
            f"UPDATE {col._objects_table} SET meta = json(?) WHERE id = ?",
            ('{"unit": "F", "location": "field"}', obj.row.id),
        )
        conn.commit()

        col.refresh(obj)
        assert obj.unit == "F"
        assert obj.location == "field"

    def test_refresh_updates_row_fields(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        col.write_object(obj)
        original_edit_time = obj.row.edit_time

        obj.unit = "F"
        col.update_object(obj)
        edit_after_update = obj.row.edit_time

        col.refresh(obj)
        assert obj.row.edit_time == edit_after_update
        assert obj.row.edit_time > original_edit_time

    def test_refresh_unpersisted_raises(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        with pytest.raises(ObjectNotPersistedError):
            col.refresh(obj)

    def test_refresh_deleted_row_raises(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        col.write_object(obj)

        conn.execute(f"DELETE FROM {col._objects_table} WHERE id = ?", (obj.row.id,))
        conn.commit()

        with pytest.raises(ObjectNotFoundError):
            col.refresh(obj)


class TestReadLatestObject:
    def test_read_latest_returns_most_recently_edited(self, conn):
        col = Collection(conn, "items")
        book_a = SampleModel(unit="A", location="shelf1")
        book_b = SampleModel(unit="B", location="shelf2")
        col.write_object(book_a)
        col.write_object(book_b)

        # Update the first object so it has a newer edit_time
        book_a.location = "shelf1_updated"
        col.update_object(book_a)

        latest = col.read_latest_object(SampleModel)
        assert latest is not None
        assert latest.unit == "A"
        assert latest.location == "shelf1_updated"

    def test_read_latest_deterministic_on_same_edit_time(self, conn):
        col = Collection(conn, "items")
        # Write two objects in tight loop — likely same edit_time on Windows
        col.write_object(SampleModel(unit="first", location="a"))
        col.write_object(SampleModel(unit="second", location="b"))

        latest = col.read_latest_object(SampleModel)
        assert latest is not None
        # Higher id wins as tiebreaker
        assert latest.row.id == 2
        assert latest.unit == "second"

    def test_read_latest_empty_collection_returns_none(self, conn):
        col = Collection(conn, "items")
        result = col.read_latest_object(SampleModel)
        assert result is None

    def test_read_latest_filters_by_model_type(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.write_object(Sensor(unit="F"))

        latest_sample = col.read_latest_object(SampleModel)
        latest_sensor = col.read_latest_object(Sensor)

        assert latest_sample is not None
        assert isinstance(latest_sample, SampleModel)
        assert latest_sample.unit == "C"

        assert latest_sensor is not None
        assert isinstance(latest_sensor, Sensor)
        assert latest_sensor.unit == "F"

    def test_read_latest_raw_returns_row(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))

        row = col.read_latest_object(SampleModel, raw=True)
        assert row is not None
        assert row["name"] == "SampleModel"

    def test_read_latest_raw_empty_returns_none(self, conn):
        col = Collection(conn, "items")
        result = col.read_latest_object(SampleModel, raw=True)
        assert result is None

    def test_read_latest_sets_persisted(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))

        obj = col.read_latest_object(SampleModel)
        assert obj is not None
        assert obj._persisted is True
        assert isinstance(obj.row, RowFields)
        assert obj.row.id == 1


class TestReadLatestObjectFiltered:
    """Tests for read_latest_object with conditions and aggregates."""

    def test_conditions_filter_results(self, conn):
        col = Collection(conn, "items")
        col.write_object(Sensor(unit="Celsius", status="active"))
        col.write_object(Sensor(unit="Fahrenheit", status="active"))
        col.write_object(Sensor(unit="Celsius", status="inactive"))

        latest = col.read_latest_object(Sensor.fields.unit == "Celsius")
        assert latest is not None
        assert latest.unit == "Celsius"
        assert latest.status == "inactive"  # id=3 is latest

    def test_multiple_conditions(self, conn):
        col = Collection(conn, "items")
        col.write_object(Sensor(unit="C", status="active", location="lab"))
        col.write_object(Sensor(unit="C", status="inactive", location="field"))
        col.write_object(Sensor(unit="C", status="active", location="roof"))

        latest = col.read_latest_object(
            Sensor.fields.unit == "C",
            Sensor.fields.status == "active",
        )
        assert latest is not None
        assert latest.location == "roof"

    def test_conditions_no_match_returns_none(self, conn):
        col = Collection(conn, "items")
        col.write_object(Sensor(unit="Celsius"))

        result = col.read_latest_object(Sensor.fields.unit == "Kelvin")
        assert result is None

    def test_model_class_with_conditions(self, conn):
        """read_latest_object(Model, condition) style."""
        col = Collection(conn, "items")
        col.write_object(Sensor(unit="C", location="a"))
        col.write_object(Sensor(unit="F", location="b"))
        col.write_object(Sensor(unit="C", location="c"))

        latest = col.read_latest_object(Sensor, Sensor.fields.unit == "C")
        assert latest is not None
        assert latest.location == "c"

    def test_raw_with_conditions(self, conn):
        col = Collection(conn, "items")
        col.write_object(Sensor(unit="C", location="lab"))
        col.write_object(Sensor(unit="F", location="field"))

        row = col.read_latest_object(Sensor.fields.unit == "C", raw=True)
        assert row is not None
        assert row["name"] == "Sensor"

    def test_max_field(self, conn):
        col = Collection(conn, "items")
        col.write_object(Sensor(unit="C", level=10.0, location="a"))
        col.write_object(Sensor(unit="C", level=50.0, location="b"))
        col.write_object(Sensor(unit="C", level=30.0, location="c"))

        latest = col.read_latest_object(
            Sensor.fields.unit == "C",
            max_field=Sensor.fields.level,
        )
        assert latest is not None
        assert latest.level == 50.0
        assert latest.location == "b"

    def test_min_field(self, conn):
        col = Collection(conn, "items")
        col.write_object(Sensor(unit="C", level=10.0, location="a"))
        col.write_object(Sensor(unit="C", level=50.0, location="b"))
        col.write_object(Sensor(unit="C", level=5.0, location="c"))

        latest = col.read_latest_object(
            Sensor.fields.unit == "C",
            min_field=Sensor.fields.level,
        )
        assert latest is not None
        assert latest.level == 5.0
        assert latest.location == "c"

    def test_max_field_returns_latest_among_ties(self, conn):
        col = Collection(conn, "items")
        s1 = Sensor(unit="C", level=50.0, location="first")
        s2 = Sensor(unit="C", level=50.0, location="second")
        col.write_object(s1)
        col.write_object(s2)

        latest = col.read_latest_object(
            Sensor.fields.unit == "C",
            max_field=Sensor.fields.level,
        )
        assert latest is not None
        # Both have level=50.0, higher id wins as tiebreaker
        assert latest.location == "second"

    def test_max_and_min_together_raises(self, conn):
        col = Collection(conn, "items")
        with pytest.raises(TypeError, match="only one"):
            col.read_latest_object(
                Sensor,
                max_field=Sensor.fields.level,
                min_field=Sensor.fields.level,
            )

    def test_no_args_raises(self, conn):
        col = Collection(conn, "items")
        with pytest.raises(TypeError, match="At least one argument"):
            col.read_latest_object()

    def test_latest_picks_most_recent_edit_among_filtered(self, conn):
        col = Collection(conn, "items")
        s1 = Sensor(unit="C", location="old")
        s2 = Sensor(unit="C", location="newer")
        col.write_object(s1)
        col.write_object(s2)

        # Update s1 so it has a more recent edit_time
        s1.location = "updated"
        col.update_object(s1)

        latest = col.read_latest_object(Sensor.fields.unit == "C")
        assert latest is not None
        assert latest.location == "updated"
        assert latest.row.id == s1.row.id

    def test_fieldproxy_style(self, conn):
        col = Collection(conn, "items")
        col.write_object(Sensor(unit="C"))
        col.write_object(Sensor(unit="F"))

        latest = col.read_latest_object(Sensor.fields)
        assert latest is not None
        assert latest.unit == "F"

    def test_fieldproxy_with_aggregate_raises(self, conn):
        col = Collection(conn, "items")
        with pytest.raises(TypeError, match="Model.fields"):
            col.read_latest_object(Sensor.fields, max_field=Sensor.fields.level)
