import pytest
from conftest import NamedModel, PlainModel, SampleModel

from centaurodb import Collection, ObjectAlreadyPersistedError


class TestWriteObject:
    def test_write_inserts_row(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))

        row = conn.execute(f"SELECT * FROM {col._objects_table}").fetchone()
        assert row["name"] == "SampleModel"
        assert '"unit":"C"' in row["meta"] or '"unit": "C"' in row["meta"]

    def test_write_sets_timestamps(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))

        row = conn.execute(f"SELECT * FROM {col._objects_table}").fetchone()
        assert row["write_time"] is not None
        assert row["edit_time"] is not None
        assert row["write_time"] == row["edit_time"]

    def test_write_rejects_plain_basemodel(self, conn):
        col = Collection(conn, "items")
        with pytest.raises(TypeError, match="Expected a CentauroModel"):
            col.write_object(PlainModel(unit="C"))  # type:ignore

    def test_write_autoincrement_id(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.write_object(SampleModel(unit="F", location="field"))

        rows = conn.execute(
            f"SELECT id FROM {col._objects_table} ORDER BY id"
        ).fetchall()
        assert rows[0]["id"] == 1
        assert rows[1]["id"] == 2

    def test_write_sets_persisted(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        assert obj._persisted is False
        col.write_object(obj)
        assert obj._persisted is True

    def test_write_sets_row_on_insert(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        col.write_object(obj)
        assert obj.row.id == 1
        assert obj.row.name == "SampleModel"

    def test_write_persisted_raises(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        col.write_object(obj)
        with pytest.raises(ObjectAlreadyPersistedError):
            col.write_object(obj)

    def test_write_duplicate_creates_new_row(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        col.write_object(obj)

        col.write_object(obj, duplicate=True)
        rows = conn.execute(
            f"SELECT id FROM {col._objects_table} ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["id"] == 1
        assert rows[1]["id"] == 2


class TestWriteObjects:
    def test_write_objects_batch(self, conn):
        col = Collection(conn, "items")
        objs = [
            SampleModel(unit="C", location="lab"),
            SampleModel(unit="F", location="field"),
        ]
        col.write_objects(objs)

        rows = conn.execute(
            f"SELECT id FROM {col._objects_table} ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert all(o._persisted for o in objs)

    def test_write_objects_sets_row_fields(self, conn):
        col = Collection(conn, "items")
        objs = [
            SampleModel(unit="C", location="lab"),
            SampleModel(unit="F", location="field"),
        ]
        col.write_objects(objs)

        assert objs[0].row.id == 1
        assert objs[1].row.id == 2
        assert objs[0].row.name == "SampleModel"

    def test_write_objects_empty_list(self, conn):
        col = Collection(conn, "items")
        col.write_objects([])

        rows = conn.execute(f"SELECT COUNT(*) FROM {col._objects_table}").fetchone()
        assert rows[0] == 0

    def test_write_objects_atomicity_rollback(self, conn):
        col = Collection(conn, "items")
        good = SampleModel(unit="C", location="lab")
        bad = PlainModel(unit="F")  # not a CentauroModel — will fail

        with pytest.raises(TypeError, match="Expected a CentauroModel"):
            col.write_objects([good, bad])  # type: ignore

        # Nothing should be persisted
        rows = conn.execute(f"SELECT COUNT(*) FROM {col._objects_table}").fetchone()
        assert rows[0] == 0

    def test_write_objects_rollback_restores_python_state(self, conn):
        col = Collection(conn, "items")
        good = SampleModel(unit="C", location="lab")
        bad = PlainModel(unit="F")  # not a CentauroModel — will fail

        assert good._persisted is False
        assert good._row is None

        with pytest.raises(TypeError, match="Expected a CentauroModel"):
            col.write_objects([good, bad])  # type: ignore

        # Python object state should be fully restored
        assert good._persisted is False
        assert good._row is None

    def test_write_objects_rejects_already_persisted(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        col.write_object(obj)

        with pytest.raises(ObjectAlreadyPersistedError):
            col.write_objects([obj])

    def test_write_objects_duplicate_flag(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        col.write_object(obj)

        col.write_objects([obj], duplicate=True)
        rows = conn.execute(
            f"SELECT id FROM {col._objects_table} ORDER BY id"
        ).fetchall()
        assert len(rows) == 2

    def test_write_objects_rejects_model_series(self, conn):
        from conftest import SampleModelSeries

        col = Collection(conn, "items")
        with pytest.raises(TypeError, match="Collection does not accept"):
            col.write_objects([SampleModelSeries(unit="C")])


class TestCentauroName:
    def test_default_uses_class_name(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        col.write_object(obj)

        row = conn.execute(f"SELECT name FROM {col._objects_table}").fetchone()
        assert row["name"] == "SampleModel"

    def test_custom_name_stored_in_db(self, conn):
        col = Collection(conn, "items")
        obj = NamedModel(value=1.0)
        col.write_object(obj)

        row = conn.execute(f"SELECT name FROM {col._objects_table}").fetchone()
        assert row["name"] == "CustomName"

    def test_custom_name_on_row_fields(self, conn):
        col = Collection(conn, "items")
        obj = NamedModel(value=1.0)
        col.write_object(obj)

        assert obj.row.name == "CustomName"

    def test_custom_name_roundtrip(self, conn):
        col = Collection(conn, "items")
        obj = NamedModel(value=42.0)
        col.write_object(obj)

        loaded = col.read_object_by_id(obj.row.id, NamedModel)
        assert loaded.value == 42.0
        assert loaded.row.name == "CustomName"
