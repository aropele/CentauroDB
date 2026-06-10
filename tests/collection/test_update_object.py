import pytest
from conftest import PlainModel, SampleModel

from centaurodb import Collection, ObjectNotPersistedError


class TestUpdateObject:
    def test_update_object(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        col.write_object(obj)
        original_write_time = obj.row.write_time

        obj.unit = "F"
        col.update_object(obj)

        updated = col.read_object_by_id(1, SampleModel)
        assert updated.unit == "F"
        assert updated.row.write_time == original_write_time
        assert updated.row.edit_time > original_write_time

    def test_update_unpersisted_raises(self, conn):
        col = Collection(conn, "items")
        obj = SampleModel(unit="C", location="lab")
        with pytest.raises(ObjectNotPersistedError):
            col.update_object(obj)

    def test_update_rejects_plain_basemodel(self, conn):
        col = Collection(conn, "items")
        with pytest.raises(TypeError, match="Expected a CentauroModel"):
            col.update_object(PlainModel(unit="C"))  # type:ignore
