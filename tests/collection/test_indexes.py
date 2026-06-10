"""Tests for BaseCollection.create_index / drop_index / list_indexes."""

import pytest

from centaurodb import (
    Collection,
    CentauroModel,
    TimeSeriesCollection,
    CentauroModelSeries,
    renamed_from,
)

from conftest import Sensor


class Metric(CentauroModelSeries):
    __centauro_name__ = "Metric"
    unit: str = ""


@pytest.fixture
def col(conn):
    return Collection(conn, "sensors")


# ---------------------------------------------------------------------------
# create_index
# ---------------------------------------------------------------------------


class TestCreateIndex:
    def test_meta_field_creates_expression_index(self, conn, col):
        col.create_index("status")

        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sensors_objects'"
        ).fetchall()
        index_names = [r[0] for r in indexes]
        assert "idx_sensors_status" in index_names

    def test_meta_field_index_is_expression_on_json(self, conn, col):
        col.create_index("status")

        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='idx_sensors_status'"
        ).fetchone()
        assert "json_extract(meta, '$.status')" in row["sql"]

    def test_plain_column_write_time(self, conn, col):
        col.create_index("write_time")

        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sensors_objects'"
        ).fetchall()
        index_names = [r[0] for r in indexes]
        assert "idx_sensors_write_time" in index_names

    def test_plain_column_index_is_not_expression(self, conn, col):
        col.create_index("write_time")

        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='idx_sensors_write_time'"
        ).fetchone()
        assert "json_extract" not in row["sql"]
        assert "write_time" in row["sql"]

    def test_plain_column_edit_time(self, conn, col):
        col.create_index("edit_time")
        assert "edit_time" in col.list_indexes()

    def test_plain_column_name_is_idempotent(self, conn, col):
        # 'name' is already indexed automatically; create_index("name") is a no-op
        col.create_index("name")  # IF NOT EXISTS — must not raise
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='idx_sensors_name'"
        ).fetchone()
        assert row is not None
        assert "json_extract" not in row["sql"]

    def test_idempotent_meta_field(self, col):
        col.create_index("status")
        col.create_index("status")  # must not raise
        assert col.list_indexes().count("status") == 1

    def test_idempotent_plain_column(self, col):
        col.create_index("write_time")
        col.create_index("write_time")  # must not raise
        assert col.list_indexes().count("write_time") == 1

    def test_multiple_indexes_created(self, col):
        col.create_index("status")
        col.create_index("location")
        col.create_index("write_time")
        indexes = col.list_indexes()
        assert "status" in indexes
        assert "location" in indexes
        assert "write_time" in indexes

    def test_timeseries_collection(self, conn):
        ts = TimeSeriesCollection(conn, "metrics")
        ts.create_index("unit")
        assert "unit" in ts.list_indexes()

    def test_timeseries_collection_plain_column(self, conn):
        ts = TimeSeriesCollection(conn, "metrics")
        ts.create_index("write_time")
        assert "write_time" in ts.list_indexes()

    @pytest.mark.parametrize(
        "bad_name",
        [
            "foo'); DROP TABLE x;--",
            "UPPER",
            "has space",
            "1starts_with_digit",
            "",
        ],
    )
    def test_rejects_invalid_field_name(self, col, bad_name):
        with pytest.raises(ValueError, match="lowercase alphanumeric"):
            col.create_index(bad_name)


# ---------------------------------------------------------------------------
# list_indexes
# ---------------------------------------------------------------------------


class TestListIndexes:
    def test_empty_when_no_user_indexes(self, col):
        # only the auto name index exists — must not appear
        assert col.list_indexes() == []

    def test_excludes_auto_name_index(self, col):
        assert "name" not in col.list_indexes()

    def test_returns_field_names_by_default(self, col):
        col.create_index("status")
        col.create_index("write_time")
        result = col.list_indexes()
        assert "status" in result
        assert "write_time" in result
        # no full index names
        assert not any(r.startswith("idx_") for r in result)

    def test_raw_returns_full_index_names(self, col):
        col.create_index("status")
        result = col.list_indexes(raw=True)
        assert "idx_sensors_status" in result

    def test_raw_excludes_auto_name_index(self, col):
        result = col.list_indexes(raw=True)
        assert "idx_sensors_name" not in result

    def test_result_is_sorted(self, col):
        col.create_index("status")
        col.create_index("location")
        col.create_index("level")
        result = col.list_indexes()
        assert result == sorted(result)

    def test_raw_result_is_sorted(self, col):
        col.create_index("status")
        col.create_index("location")
        col.create_index("level")
        result = col.list_indexes(raw=True)
        assert result == sorted(result)

    def test_includes_indexes_from_create_index(self, conn, col):
        """Indexes created via create_index appear in list_indexes."""
        col.create_index("location")
        assert "location" in col.list_indexes()

    def test_different_collections_do_not_interfere(self, conn):
        col_a = Collection(conn, "alpha")
        col_b = Collection(conn, "beta")
        col_a.create_index("status")
        assert "status" in col_a.list_indexes()
        assert "status" not in col_b.list_indexes()


# ---------------------------------------------------------------------------
# drop_index
# ---------------------------------------------------------------------------


class TestDropIndex:
    def test_drop_removes_index(self, col):
        col.create_index("status")
        assert "status" in col.list_indexes()
        col.drop_index("status")
        assert "status" not in col.list_indexes()

    def test_drop_plain_column_index(self, col):
        col.create_index("write_time")
        col.drop_index("write_time")
        assert "write_time" not in col.list_indexes()

    def test_drop_is_silent_when_missing(self, col):
        col.drop_index("nonexistent")  # must not raise

    def test_drop_managed_name_index_raises(self, col):
        with pytest.raises(ValueError, match="auto-managed"):
            col.drop_index("name")

    def test_drop_managed_name_index_leaves_index_intact(self, conn, col):
        with pytest.raises(ValueError):
            col.drop_index("name")
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_sensors_name'"
        ).fetchone()
        assert row is not None  # auto-managed index is unaffected

    def test_timeseries_list_indexes(self, conn):
        ts = TimeSeriesCollection(conn, "metrics")
        ts.create_index("unit")
        ts.create_index("write_time")
        result = ts.list_indexes()
        assert "unit" in result
        assert "write_time" in result
        assert "name" not in result

    def test_timeseries_drop_index(self, conn):
        ts = TimeSeriesCollection(conn, "metrics")
        ts.create_index("unit")
        ts.drop_index("unit")
        assert "unit" not in ts.list_indexes()

    def test_timeseries_drop_managed_name_index_raises(self, conn):
        ts = TimeSeriesCollection(conn, "metrics")
        with pytest.raises(ValueError, match="auto-managed"):
            ts.drop_index("name")

    def test_drop_then_recreate(self, col):
        col.create_index("status")
        col.drop_index("status")
        col.create_index("status")  # must not raise
        assert "status" in col.list_indexes()

    def test_drop_index_created_by_create_index(self, conn, col):
        """drop_index can remove indexes created by create_index."""
        col.create_index("location")
        assert "location" in col.list_indexes()
        col.drop_index("location")
        assert "location" not in col.list_indexes()

    def test_drop_does_not_affect_other_indexes(self, col):
        col.create_index("status")
        col.create_index("location")
        col.drop_index("status")
        assert "location" in col.list_indexes()
        assert "status" not in col.list_indexes()

    def test_drop_rejects_invalid_field_name(self, col):
        with pytest.raises(ValueError, match="lowercase alphanumeric"):
            col.drop_index("foo'); DROP TABLE x;--")


# ---------------------------------------------------------------------------
# Query plan — index is actually used by SQLite
# ---------------------------------------------------------------------------


class TestIndexQueryPlan:
    def test_meta_field_index_used_in_query_plan(self, conn, col):
        col.write_object(Sensor(status="active"))
        col.create_index("status")

        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM sensors_objects "
            "WHERE json_extract(meta, '$.status') = 'active'"
        ).fetchall()
        plan_text = " ".join(r["detail"] for r in plan)
        assert "idx_sensors_status" in plan_text

    def test_plain_column_index_used_in_query_plan(self, conn, col):
        col.write_object(Sensor())
        col.create_index("write_time")

        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM sensors_objects "
            "WHERE write_time > '2020-01-01'"
        ).fetchall()
        plan_text = " ".join(r["detail"] for r in plan)
        assert "idx_sensors_write_time" in plan_text


# ---------------------------------------------------------------------------
# Field rename — stale index behaviour
# ---------------------------------------------------------------------------


class TestStaleIndexAfterRename:
    def test_old_index_still_exists_after_rename(self, conn):
        """
        An index on 'temp' survives the field rename — it is stale but not broken.
        New rows written with 'temperature' are not covered by idx_sensors_temp.
        """

        class SensorV1(CentauroModel):
            __centauro_name__ = "Sensor"
            temp: float = 0.0

        class SensorV2(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", default=0.0)

        col = Collection(conn, "sensors")
        col.create_index("temp")
        assert "temp" in col.list_indexes()

        # After rename, the index on '$.temp' is stale — it still exists
        col.write_object(SensorV2(temperature=25.0))
        assert "temp" in col.list_indexes()  # stale index still present

    def test_rotate_index_after_rename(self, conn):
        """
        Correct migration: create new index on renamed key, drop stale one.
        """

        class SensorV1(CentauroModel):
            __centauro_name__ = "Sensor"
            temp: float = 0.0

        class SensorV2(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", default=0.0)

        col = Collection(conn, "sensors")
        col.create_index("temp")

        # Rename happens in application code; rotate the index
        col.create_index("temperature")
        col.drop_index("temp")

        indexes = col.list_indexes()
        assert "temperature" in indexes
        assert "temp" not in indexes

    def test_new_index_covers_new_rows_after_rename(self, conn):
        """
        After rotating the index, queries on the new field name use it.
        """

        class SensorV2(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", default=0.0)

        col = Collection(conn, "sensors")
        col.write_object(SensorV2(temperature=99.0))
        col.create_index("temperature")

        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM sensors_objects "
            "WHERE json_extract(meta, '$.temperature') = 99.0"
        ).fetchall()
        plan_text = " ".join(r["detail"] for r in plan)
        assert "idx_sensors_temperature" in plan_text
