"""Tests for SQLite schema internals: foreign keys, cascades, multi-collection isolation.

These test the raw table structure that will be hidden behind utility functions.
"""

from centaurodb import Collection, CentauroValues, TimeSeriesCollection

from conftest import SampleModelSeries, Sensor, get_column_names, get_column_types


class TestCascadeDelete:
    def test_deleting_object_removes_values(self, conn):
        col = TimeSeriesCollection(conn, "items")
        col.write_object(
            SampleModelSeries(
                unit="C",
                values=[CentauroValues(time="2025-01-01T00:00:00", value=20.5)],
            )
        )

        conn.execute(f"DELETE FROM {col._objects_table} WHERE id = ?", (1,))
        conn.commit()

        count = conn.execute(f"SELECT COUNT(*) FROM {col._values_table}").fetchone()[0]
        assert count == 0


class TestMultipleCollections:
    def test_collections_coexist(self, conn):
        col_a = TimeSeriesCollection(conn, "sensors")
        col_b = Collection(conn, "alerts")

        col_a.write_object(SampleModelSeries(unit="C"))
        col_b.write_object(Sensor(unit="F"))

        sensors_count = conn.execute(
            f"SELECT COUNT(*) FROM {col_a._objects_table}"
        ).fetchone()[0]
        alerts_count = conn.execute(
            f"SELECT COUNT(*) FROM {col_b._objects_table}"
        ).fetchone()[0]
        assert sensors_count == 1
        assert alerts_count == 1


class TestJsonMeta:
    def test_meta_column_type_is_json(self, conn):
        Collection(conn, "items")
        col_types = get_column_types(conn, "items_objects")
        assert col_types["meta"] == "JSON"

    def test_json_extract_works_on_meta(self, conn):
        col = Collection(conn, "items")
        col.write_object(Sensor(unit="C"))

        result = conn.execute(
            f"SELECT json_extract(meta, '$.unit') AS unit FROM {col._objects_table} WHERE id = ?",
            (1,),
        ).fetchone()
        assert result["unit"] == "C"


class TestValuesTableSchema:
    def test_values_table_has_write_time_column(self, conn):
        TimeSeriesCollection(conn, "items")
        columns = get_column_names(conn, "items_values")
        assert "write_time" in columns

    def test_write_time_column_type_is_datetime(self, conn):
        TimeSeriesCollection(conn, "items")
        col_types = get_column_types(conn, "items_values")
        assert col_types["write_time"] == "DATETIME"


class TestIndexes:
    def test_name_index_exists(self, conn):
        Collection(conn, "items")
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='items_objects'"
        ).fetchall()
        index_names = [r[0] for r in indexes]
        assert "idx_items_name" in index_names

    def test_name_index_covers_name_column(self, conn):
        Collection(conn, "items")
        info = conn.execute("PRAGMA index_info(idx_items_name)").fetchall()
        columns = [r["name"] for r in info]
        assert columns == ["name"]
