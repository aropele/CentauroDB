"""Tests for CRUD operations using collection methods."""

from centaurodb import Collection, CentauroValues, TimeSeriesCollection

from conftest import SampleModelSeries, Sensor


class TestObjectsCRUD:
    def test_write_and_read_object(self, conn):
        col = Collection(conn, "items")
        col.write_object(Sensor(unit="C"))

        obj = col.read_object_by_id(1, Sensor)
        assert obj.unit == "C"

    def test_read_raw_preserves_metadata(self, conn):
        col = Collection(conn, "items")
        col.write_object(Sensor(unit="C"))

        row = col.read_object_by_id(1, Sensor, raw=True)
        assert row["name"] == "Sensor"
        assert row["id"] == 1


class TestObjectsWithValues:
    def test_join_objects_and_values(self, conn):
        col = TimeSeriesCollection(conn, "metrics")
        col.write_object(
            SampleModelSeries(
                unit="percent",
                values=[
                    CentauroValues(time="2025-01-01T00:00:00", value=45.2),
                    CentauroValues(time="2025-01-01T00:01:00", value=47.8),
                ],
            )
        )

        rows = conn.execute(
            f"SELECT o.name, v.time, v.value "
            f"FROM {col._objects_table} o "
            f"JOIN {col._values_table} v ON o.id = v.data_object_id "
            f"WHERE o.id = ? ORDER BY v.time",
            (1,),
        ).fetchall()

        assert len(rows) == 2
        assert rows[0]["name"] == "SampleModelSeries"
        assert rows[0]["value"] == 45.2
        assert rows[1]["value"] == 47.8
