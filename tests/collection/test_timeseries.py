"""Tests for CentauroModelSeries write/read/update through TimeSeriesCollection."""

import json
from datetime import datetime

import pytest

import polars as pl

from centaurodb import (
    Collection,
    CentauroValues,
    ObjectAlreadyPersistedError,
    ObjectNotFoundError,
    ObjectNotPersistedError,
    RowFields,
    TimeSeriesCollection,
    ValuesNotHydratedError,
)

from conftest import NamedModelSeries, PlainModel, SampleModelSeries, Sensor


class TestModelTypeGuards:
    def test_timeseries_collection_rejects_plain_model(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        with pytest.raises(
            TypeError, match="TimeSeriesCollection requires CentauroModelSeries"
        ):
            col.write_object(Sensor(unit="C"))

    def test_collection_rejects_model_series(self, conn):
        col = Collection(conn, "sensors")
        with pytest.raises(
            TypeError, match="Collection does not accept CentauroModelSeries"
        ):
            col.write_object(SampleModelSeries(unit="C"))


class TestWriteObjectWithValues:
    def test_write_persists_object_and_values(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[
                CentauroValues(time="2025-01-01T00:00:00", value=20.5),
                CentauroValues(time="2025-01-01T01:00:00", value=21.0),
            ],
        )
        col.write_object(obj)

        count = conn.execute(f"SELECT COUNT(*) FROM {col._values_table}").fetchone()[0]
        assert count == 2

    def test_write_values_have_write_time(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        col.write_object(obj)

        row = conn.execute(
            f"SELECT write_time FROM {col._values_table} LIMIT 1"
        ).fetchone()
        assert row["write_time"] is not None

    def test_values_excluded_from_meta_json(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        col.write_object(obj)

        row = conn.execute(
            f"SELECT meta FROM {col._objects_table} WHERE id = ?", (obj.row.id,)
        ).fetchone()
        meta = json.loads(row["meta"])
        assert "values" not in meta
        assert meta["unit"] == "C"

    def test_write_with_empty_values(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(unit="C", values=[])
        col.write_object(obj)

        count = conn.execute(f"SELECT COUNT(*) FROM {col._values_table}").fetchone()[0]
        assert count == 0
        assert obj._persisted

    def test_write_sets_timestamps(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(unit="C")
        col.write_object(obj)

        row = conn.execute(f"SELECT * FROM {col._objects_table}").fetchone()
        assert row["write_time"] is not None
        assert row["edit_time"] is not None
        assert row["write_time"] == row["edit_time"]

    def test_write_autoincrement_id(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(SampleModelSeries(unit="C"))
        col.write_object(SampleModelSeries(unit="F"))

        rows = conn.execute(
            f"SELECT id FROM {col._objects_table} ORDER BY id"
        ).fetchall()
        assert rows[0]["id"] == 1
        assert rows[1]["id"] == 2

    def test_write_sets_persisted(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(unit="C")
        assert obj._persisted is False
        col.write_object(obj)
        assert obj._persisted is True

    def test_write_sets_row_on_insert(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(unit="C")
        col.write_object(obj)
        assert obj.row.id == 1
        assert obj.row.name == "SampleModelSeries"

    def test_write_persisted_raises(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(unit="C")
        col.write_object(obj)
        with pytest.raises(ObjectAlreadyPersistedError):
            col.write_object(obj)

    def test_write_duplicate_creates_new_row(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        col.write_object(obj)

        col.write_object(obj, duplicate=True)
        rows = conn.execute(
            f"SELECT id FROM {col._objects_table} ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["id"] == 1
        assert rows[1]["id"] == 2

    def test_write_rejects_plain_basemodel(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        with pytest.raises(TypeError):
            col.write_object(PlainModel(unit="C"))  # type:ignore


class TestWriteObjectsTimeSeries:
    def test_batch_write_persists_objects_and_values(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        objs = [
            SampleModelSeries(
                unit="C",
                values=[
                    CentauroValues(time="2025-01-01T00:00:00", value=20.5),
                    CentauroValues(time="2025-01-01T01:00:00", value=21.0),
                ],
            ),
            SampleModelSeries(
                unit="F",
                values=[
                    CentauroValues(time="2025-01-01T02:00:00", value=70.0),
                ],
            ),
        ]
        col.write_objects(objs)

        obj_count = conn.execute(
            f"SELECT COUNT(*) FROM {col._objects_table}"
        ).fetchone()[0]
        assert obj_count == 2

        val_count = conn.execute(
            f"SELECT COUNT(*) FROM {col._values_table}"
        ).fetchone()[0]
        assert val_count == 3

        assert all(o._persisted for o in objs)

    def test_batch_write_sets_row_fields(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        objs = [
            SampleModelSeries(unit="C"),
            SampleModelSeries(unit="F"),
        ]
        col.write_objects(objs)

        assert objs[0].row.id == 1
        assert objs[1].row.id == 2
        assert objs[0].row.name == "SampleModelSeries"

    def test_batch_write_empty_list(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_objects([])

        count = conn.execute(f"SELECT COUNT(*) FROM {col._objects_table}").fetchone()[0]
        assert count == 0

    def test_batch_write_atomicity_rollback(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        good = SampleModelSeries(unit="C")
        bad = Sensor(unit="F")  # not a CentauroModelSeries — will fail

        with pytest.raises(TypeError, match="TimeSeriesCollection requires"):
            col.write_objects([good, bad])

        # Nothing should be persisted
        count = conn.execute(f"SELECT COUNT(*) FROM {col._objects_table}").fetchone()[0]
        assert count == 0

    def test_batch_write_rollback_restores_python_state(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        good = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        bad = Sensor(unit="F")  # not a CentauroModelSeries — will fail

        assert good._persisted is False
        assert good._row is None

        with pytest.raises(TypeError, match="TimeSeriesCollection requires"):
            col.write_objects([good, bad])

        # Python object state should be fully restored
        assert good._persisted is False
        assert good._row is None

    def test_batch_write_rejects_already_persisted(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(unit="C")
        col.write_object(obj)

        with pytest.raises(ObjectAlreadyPersistedError):
            col.write_objects([obj])

    def test_batch_write_duplicate_flag(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        col.write_object(obj)

        col.write_objects([obj], duplicate=True)
        rows = conn.execute(
            f"SELECT id FROM {col._objects_table} ORDER BY id"
        ).fetchall()
        assert len(rows) == 2

    def test_batch_write_with_empty_values(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        objs = [
            SampleModelSeries(unit="C", values=[]),
            SampleModelSeries(unit="F", values=[]),
        ]
        col.write_objects(objs)

        obj_count = conn.execute(
            f"SELECT COUNT(*) FROM {col._objects_table}"
        ).fetchone()[0]
        assert obj_count == 2

        val_count = conn.execute(
            f"SELECT COUNT(*) FROM {col._values_table}"
        ).fetchone()[0]
        assert val_count == 0

    def test_batch_write_rejects_plain_model(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        with pytest.raises(TypeError):
            col.write_objects([Sensor(unit="C")])


class TestReadObjectWithValues:
    def test_read_with_hydrate_values_true(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[
                CentauroValues(time="2025-01-01T00:00:00", value=20.5),
                CentauroValues(time="2025-01-01T01:00:00", value=21.0),
            ],
        )
        col.write_object(obj)

        loaded = col.read_object_by_id(
            obj.row.id, SampleModelSeries, hydrate_values=True
        )
        assert len(loaded.values) == 2
        assert loaded.values[0].value == 20.5

    def test_read_with_hydrate_values_false(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        col.write_object(obj)

        loaded = col.read_object_by_id(
            obj.row.id, SampleModelSeries, hydrate_values=False
        )
        assert loaded.values == []

    def test_read_defaults_to_hydrated(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        col.write_object(obj)

        loaded = col.read_object_by_id(obj.row.id, SampleModelSeries)
        assert len(loaded.values) == 1

    def test_refresh_hydrates_values(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        col.write_object(obj)

        loaded = col.read_object_by_id(
            obj.row.id, SampleModelSeries, hydrate_values=False
        )
        assert loaded.values == []

        col.refresh(loaded)
        assert len(loaded.values) == 1
        assert loaded.values[0].value == 20.5

    def test_read_populates_row_fields(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(unit="C")
        col.write_object(obj)

        loaded = col.read_object_by_id(obj.row.id, SampleModelSeries)
        assert isinstance(loaded.row, RowFields)
        assert loaded.row.id == 1
        assert loaded.row.name == "SampleModelSeries"
        assert isinstance(loaded.row.write_time, datetime)
        assert isinstance(loaded.row.edit_time, datetime)

    def test_read_sets_persisted(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(SampleModelSeries(unit="C"))

        loaded = col.read_object_by_id(1, SampleModelSeries)
        assert loaded._persisted is True

    def test_read_raw_returns_row(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(SampleModelSeries(unit="C"))

        row = col.read_object_by_id(1, SampleModelSeries, raw=True)
        assert row["name"] == "SampleModelSeries"

    def test_read_missing_raises(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        with pytest.raises(ObjectNotFoundError, match="not found"):
            col.read_object_by_id(999, SampleModelSeries)


class TestUpdateObjectValuesMode:
    def _make_obj(self, col):
        obj = SampleModelSeries(
            unit="C",
            values=[
                CentauroValues(time="2025-01-01T00:00:00", value=20.5),
                CentauroValues(time="2025-01-01T01:00:00", value=21.0),
            ],
        )
        col.write_object(obj)
        return obj

    def test_update_also_updates_meta(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = self._make_obj(col)

        obj.unit = "F"
        obj.values = []
        col.update_object(obj)

        loaded = col.read_object_by_id(obj.row.id, SampleModelSeries)
        assert loaded.unit == "F"

    def test_update_preserves_write_time(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = self._make_obj(col)
        original_write_time = obj.row.write_time

        obj.unit = "F"
        obj.values = []
        col.update_object(obj)

        loaded = col.read_object_by_id(obj.row.id, SampleModelSeries)
        assert loaded.row.write_time == original_write_time
        assert loaded.row.edit_time > original_write_time

    def test_update_unpersisted_raises(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(unit="C")
        with pytest.raises(ObjectNotPersistedError):
            col.update_object(obj)

    def test_update_rejects_plain_basemodel(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        with pytest.raises(TypeError):
            col.update_object(PlainModel(unit="C"))  # type:ignore

    def test_update_replace_removes_old_values(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = self._make_obj(col)

        obj.values = [
            CentauroValues(time="2025-01-01T02:00:00", value=22.0),
        ]
        col.update_object(obj, values_mode="replace")

        loaded = col.read_object_by_id(obj.row.id, SampleModelSeries)
        assert len(loaded.values) == 1
        assert loaded.values[0].time == datetime(2025, 1, 1, 2, 0)
        assert loaded.values[0].value == 22.0

    def test_update_append_adds_new_ignores_duplicates(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = self._make_obj(col)

        obj.values = [
            CentauroValues(time="2025-01-01T00:00:00", value=99.0),  # duplicate PK
            CentauroValues(time="2025-01-01T02:00:00", value=22.0),  # new
        ]
        col.update_object(obj, values_mode="append")

        loaded = col.read_object_by_id(obj.row.id, SampleModelSeries)
        assert len(loaded.values) == 3
        # original value kept, not overwritten
        assert loaded.values[0].value == 20.5

    def test_update_upsert_updates_existing_adds_new(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = self._make_obj(col)

        obj.values = [
            CentauroValues(time="2025-01-01T00:00:00", value=99.0),  # update
            CentauroValues(time="2025-01-01T02:00:00", value=22.0),  # new
        ]
        col.update_object(obj, values_mode="upsert")

        loaded = col.read_object_by_id(obj.row.id, SampleModelSeries)
        assert len(loaded.values) == 3
        # existing value updated
        assert loaded.values[0].value == 99.0

    def test_upsert_updates_write_time(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = self._make_obj(col)

        ts = datetime(2025, 1, 1, 0, 0)
        original_wt = conn.execute(
            f"SELECT write_time FROM {col._values_table} "
            "WHERE data_object_id = ? AND time = ?",
            (obj.row.id, ts),
        ).fetchone()["write_time"]

        obj.values = [
            CentauroValues(time=ts, value=99.0),
        ]
        col.update_object(obj, values_mode="upsert")

        new_wt = conn.execute(
            f"SELECT write_time FROM {col._values_table} "
            "WHERE data_object_id = ? AND time = ?",
            (obj.row.id, ts),
        ).fetchone()["write_time"]
        assert new_wt >= original_wt

    def test_update_replace_with_empty_values_deletes_all(self, conn):
        """Replacing with empty values list deletes all existing values."""
        col = TimeSeriesCollection(conn, "sensors")
        obj = self._make_obj(col)

        obj.values = []
        col.update_object(obj, values_mode="replace")

        loaded = col.read_object_by_id(obj.row.id, SampleModelSeries)
        assert len(loaded.values) == 0

    def test_update_invalid_values_mode_raises(self, conn):
        """Passing an invalid values_mode string raises ValueError."""
        col = TimeSeriesCollection(conn, "sensors")
        obj = self._make_obj(col)

        with pytest.raises(ValueError, match="Invalid values_mode"):
            col.update_object(obj, values_mode="merge")


class TestCentauroNameTimeSeries:
    def test_default_uses_class_name(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(unit="C")
        col.write_object(obj)

        row = conn.execute(f"SELECT name FROM {col._objects_table}").fetchone()
        assert row["name"] == "SampleModelSeries"

    def test_custom_name_stored_in_db(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = NamedModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=1.0)],
        )
        col.write_object(obj)

        row = conn.execute(f"SELECT name FROM {col._objects_table}").fetchone()
        assert row["name"] == "CustomSeries"

    def test_custom_name_on_row_fields(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = NamedModelSeries(unit="C")
        col.write_object(obj)

        assert obj.row.name == "CustomSeries"

    def test_custom_name_roundtrip(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = NamedModelSeries(
            unit="F",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=42.0)],
        )
        col.write_object(obj)

        loaded = col.read_object_by_id(obj.row.id, NamedModelSeries)
        assert loaded.unit == "F"
        assert loaded.row.name == "CustomSeries"
        assert len(loaded.values) == 1


class TestValuesDataFrame:
    def test_df_returns_polars_dataframe(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[
                CentauroValues(time="2025-01-01T00:00:00", value=20.5),
                CentauroValues(time="2025-01-01T01:00:00", value=21.0),
            ],
        )
        col.write_object(obj)

        loaded = col.read_object_by_id(obj.row.id, SampleModelSeries)
        df = loaded.df
        assert isinstance(df, pl.DataFrame)
        assert df.shape == (2, 2)
        assert df.columns == ["time", "value"]

    def test_df_values_match(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[
                CentauroValues(time="2025-01-01T00:00:00", value=20.5),
            ],
        )
        col.write_object(obj)

        loaded = col.read_object_by_id(obj.row.id, SampleModelSeries)
        df = loaded.df
        assert df["time"][0] == datetime(2025, 1, 1, 0, 0)
        assert df["value"][0] == 20.5

    def test_df_empty_values_returns_empty_dataframe(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(unit="C", values=[])
        col.write_object(obj)

        loaded = col.read_object_by_id(obj.row.id, SampleModelSeries)
        assert loaded.df.shape == (0, 2)

    def test_df_raises_when_not_hydrated(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        col.write_object(obj)

        loaded = col.read_object_by_id(
            obj.row.id, SampleModelSeries, hydrate_values=False
        )
        with pytest.raises(ValuesNotHydratedError, match="hydrate_values"):
            loaded.df

    def test_df_works_after_manual_hydration(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        col.write_object(obj)

        loaded = col.read_object_by_id(
            obj.row.id, SampleModelSeries, hydrate_values=False
        )
        col.refresh(loaded)
        df = loaded.df
        assert df.shape == (1, 2)

    def test_df_on_fresh_object_before_write(self, conn):
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        df = obj.df
        assert df.shape == (1, 2)


class TestDataFrameInput:
    def test_create_from_polars_dataframe(self, conn):
        df = pl.DataFrame(
            {
                "time": [datetime(2025, 1, 1, 0, 0), datetime(2025, 1, 1, 1, 0)],
                "value": [20.5, 21.0],
            }
        )
        obj = SampleModelSeries(unit="C", values=df)
        assert len(obj.values) == 2
        assert obj.values[0].time == datetime(2025, 1, 1, 0, 0)
        assert obj.values[0].value == 20.5

    def test_dataframe_input_roundtrips_through_db(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        df = pl.DataFrame(
            {
                "time": [datetime(2025, 1, 1, 0, 0), datetime(2025, 1, 1, 1, 0)],
                "value": [20.5, 21.0],
            }
        )
        obj = SampleModelSeries(unit="C", values=df)
        col.write_object(obj)

        loaded = col.read_object_by_id(obj.row.id, SampleModelSeries)
        assert len(loaded.values) == 2
        assert loaded.values[1].value == 21.0

    def test_dataframe_assign_to_values(self, conn):
        obj = SampleModelSeries(unit="C")
        df = pl.DataFrame(
            {
                "time": [datetime(2025, 1, 1, 0, 0)],
                "value": [42.0],
            }
        )
        obj.values = df
        assert len(obj.values) == 1
        assert obj.values[0].value == 42.0

    def test_empty_dataframe(self, conn):
        df = pl.DataFrame(
            {"time": [], "value": []}, schema={"time": pl.Datetime, "value": pl.Float64}
        )
        obj = SampleModelSeries(unit="C", values=df)
        assert obj.values == []

    def test_dataframe_coerces_string_times(self, conn):
        df = pl.DataFrame(
            {"time": ["2025-01-01T00:00:00"], "value": [20.5]},
            schema={"time": pl.Utf8, "value": pl.Float64},
        )
        obj = SampleModelSeries(unit="C", values=df)
        assert obj.values[0].time == datetime(2025, 1, 1, 0, 0)

    def test_dataframe_rejects_invalid_types(self, conn):
        df = pl.DataFrame(
            {"time": ["not-a-date"], "value": [20.5]},
            schema={"time": pl.Utf8, "value": pl.Float64},
        )
        with pytest.raises(Exception):
            SampleModelSeries(unit="C", values=df)

    def test_dataframe_values_are_centauro_values(self, conn):
        df = pl.DataFrame(
            {
                "time": [datetime(2025, 1, 1, 0, 0)],
                "value": [20.5],
            }
        )
        obj = SampleModelSeries(unit="C", values=df)
        assert isinstance(obj.values[0], CentauroValues)


class TestRefreshTimeSeries:
    def test_refresh_updates_meta_and_values(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        col.write_object(obj)

        conn.execute(
            f"UPDATE {col._objects_table} SET meta = json(?) WHERE id = ?",
            ('{"unit": "F"}', obj.row.id),
        )
        conn.execute(
            f"INSERT INTO {col._values_table} (data_object_id, time, value, write_time) "
            "VALUES (?, ?, ?, ?)",
            (obj.row.id, "2025-01-01T02:00:00", 99.0, "2025-01-01T00:00:00"),
        )
        conn.commit()

        col.refresh(obj)
        assert obj.unit == "F"
        assert len(obj.values) == 2

    def test_refresh_sets_hydrated_flag(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        col.write_object(obj)

        loaded = col.read_object_by_id(
            obj.row.id, SampleModelSeries, hydrate_values=False
        )
        assert loaded._values_hydrated is False

        col.refresh(loaded)
        assert loaded._values_hydrated is True
        assert len(loaded.values) == 1


class TestReadLatestObjectTimeSeries:
    def test_read_latest_object_hydrates_values(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
            )
        )
        col.write_object(
            SampleModelSeries(
                unit="F",
                values=[
                    CentauroValues(time="2025-01-01T01:00:00", value=30.0),
                    CentauroValues(time="2025-01-01T02:00:00", value=31.0),
                ],
            )
        )

        latest = col.read_latest_object(SampleModelSeries)
        assert latest is not None
        assert latest.unit == "F"
        assert len(latest.values) == 2
        assert latest.values[0].value == 30.0

    def test_read_latest_object_no_hydrate(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
            )
        )

        latest = col.read_latest_object(SampleModelSeries, hydrate_values=False)
        assert latest is not None
        assert latest.unit == "C"
        assert latest.values == []
        assert latest._values_hydrated is False

    def test_read_latest_object_raw_skips_hydration(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
            )
        )

        row = col.read_latest_object(SampleModelSeries, raw=True)
        assert row is not None
        assert row["name"] == "SampleModelSeries"

    def test_read_latest_object_empty_returns_none(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        result = col.read_latest_object(SampleModelSeries)
        assert result is None

    def test_read_latest_object_with_conditions(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
            )
        )
        col.write_object(
            SampleModelSeries(
                unit="F",
                values=[CentauroValues(time="2025-01-01T01:00:00", value=68.0)],
            )
        )
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T02:00:00", value=22.0)],
            )
        )

        latest = col.read_latest_object(SampleModelSeries.fields.unit == "C")
        assert latest is not None
        assert latest.unit == "C"
        assert latest.row.id == 3
        assert len(latest.values) == 1
        assert latest.values[0].value == 22.0

    def test_read_latest_object_with_conditions_no_hydrate(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
            )
        )

        latest = col.read_latest_object(
            SampleModelSeries.fields.unit == "C",
            hydrate_values=False,
        )
        assert latest is not None
        assert latest.unit == "C"
        assert latest.values == []
        assert latest._values_hydrated is False


class TestReadObjectsHydrateValues:
    def test_read_objects_hydrates_values_by_default(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
            )
        )
        col.write_object(
            SampleModelSeries(
                unit="F",
                values=[
                    CentauroValues(time="2025-01-01T01:00:00", value=30.0),
                    CentauroValues(time="2025-01-01T02:00:00", value=31.0),
                ],
            )
        )

        results = col.read_objects(SampleModelSeries)
        assert len(results) == 2
        assert len(results[0].values) == 1
        assert results[0].values[0].value == 20.5
        assert len(results[1].values) == 2
        assert results[1]._values_hydrated is True

    def test_read_objects_no_hydrate(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
            )
        )
        col.write_object(
            SampleModelSeries(
                unit="F",
                values=[CentauroValues(time="2025-01-01T01:00:00", value=30.0)],
            )
        )

        results = col.read_objects(SampleModelSeries, hydrate_values=False)
        assert len(results) == 2
        for obj in results:
            assert obj.values == []
            assert obj._values_hydrated is False

    def test_read_objects_no_hydrate_then_refresh(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
            )
        )

        results = col.read_objects(SampleModelSeries, hydrate_values=False)
        assert len(results) == 1
        assert results[0].values == []

        col.refresh(results[0])
        assert len(results[0].values) == 1
        assert results[0].values[0].value == 20.5
        assert results[0]._values_hydrated is True

    def test_read_objects_raw_skips_hydration(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
            )
        )

        results = col.read_objects(SampleModelSeries, raw=True)
        assert len(results) == 1
        assert not isinstance(results[0], SampleModelSeries)
        assert "meta" in results[0].keys()

    def test_read_objects_empty_collection(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        results = col.read_objects(SampleModelSeries)
        assert results == []

    def test_read_objects_hydrate_values_not_hydrated_error(self, conn):
        """Accessing .df on a non-hydrated object from read_objects raises."""
        col = TimeSeriesCollection(conn, "sensors")
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
            )
        )

        results = col.read_objects(SampleModelSeries, hydrate_values=False)
        with pytest.raises(ValuesNotHydratedError):
            _ = results[0].df


class TestUpdateObjectAtomicity:
    """Verify that update_object rolls back both meta and values on failure."""

    def test_update_rolls_back_meta_on_values_failure(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        obj = SampleModelSeries(
            unit="C",
            values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
        )
        col.write_object(obj)
        original_edit_time = obj.row.edit_time

        # Change meta and provide values that will cause a DB error.
        # We'll corrupt the values table to trigger an insertion failure.
        obj.unit = "F"
        obj.values = [CentauroValues(time="2025-01-01T01:00:00", value=99.0)]

        # Drop the values table to force an error during values insertion
        conn.execute(f"DROP TABLE {col._values_table}")

        with pytest.raises(Exception):
            col.update_object(obj, values_mode="append")

        # The meta change should have been rolled back — edit_time restored
        assert obj._row.edit_time == original_edit_time

        # Recreate the values table and verify the meta wasn't committed
        # by reading from the objects table directly
        row = conn.execute(
            f"SELECT meta FROM {col._objects_table} WHERE id = ?", (obj.row.id,)
        ).fetchone()
        meta = json.loads(row["meta"])
        assert meta["unit"] == "C"  # original, not "F"
