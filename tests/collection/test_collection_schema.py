import pytest

from centaurodb import Collection, TimeSeriesCollection

from conftest import get_tables, get_column_names, get_column_types


class TestNameValidation:
    def test_valid_names(self, conn):
        for name in ("foo", "my_data", "a1", "abc_123_def"):
            Collection(conn, name)

    @pytest.mark.parametrize(
        "name",
        ["Foo", "1abc", "foo-bar", "foo bar", "", "_leading", "UPPER"],
    )
    def test_invalid_names_raise(self, conn, name):
        with pytest.raises(ValueError, match="Collection name must be"):
            Collection(conn, name)

    def test_invalid_names_raise_timeseries(self, conn):
        with pytest.raises(ValueError, match="Collection name must be"):
            TimeSeriesCollection(conn, "Bad-Name")


class TestCollectionTableCreation:
    def test_objects_table_created(self, conn):
        Collection(conn, "items")
        tables = get_tables(conn)
        assert "items_objects" in tables

    def test_objects_table_schema(self, conn):
        Collection(conn, "items")
        columns = get_column_names(conn, "items_objects")
        assert columns == ["id", "name", "write_time", "edit_time", "meta"]

    def test_column_types(self, conn):
        Collection(conn, "items")
        col_types = get_column_types(conn, "items_objects")
        assert col_types["write_time"] == "DATETIME"
        assert col_types["edit_time"] == "DATETIME"
        assert col_types["meta"] == "JSON"

    def test_no_values_table(self, conn):
        Collection(conn, "items")
        tables = get_tables(conn)
        assert "items_values" not in tables


class TestTimeSeriesCollectionTableCreation:
    def test_both_tables_created(self, conn):
        TimeSeriesCollection(conn, "items")
        tables = get_tables(conn)
        assert "items_objects" in tables
        assert "items_values" in tables

    def test_values_table_schema(self, conn):
        TimeSeriesCollection(conn, "items")
        columns = get_column_names(conn, "items_values")
        assert columns == ["data_object_id", "time", "value", "write_time"]

    def test_values_datetime_column_type(self, conn):
        TimeSeriesCollection(conn, "items")
        col_types = get_column_types(conn, "items_values")
        assert col_types["time"] == "DATETIME"

    def test_table_name_pattern(self, conn):
        col = TimeSeriesCollection(conn, "sensors")
        assert col._objects_table == "sensors_objects"
        assert col._values_table == "sensors_values"
