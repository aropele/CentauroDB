import pytest

from centaurodb import Collection, CentauroModel, ViewAlreadyExistsError, renamed_from

from conftest import SampleModel


class Alert(CentauroModel):
    __centauro_name__ = "Alert"
    level: int = 0
    message: str = ""


class TestCreateView:
    def test_view_is_created(self, conn):
        col = Collection(conn, "sensors")
        col.create_view("dash", SampleModel)

        views = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()
        view_names = [r[0] for r in views]
        assert "sensors_view_dash" in view_names

    def test_view_columns(self, conn):
        col = Collection(conn, "sensors")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.create_view("dash", SampleModel)

        row = conn.execute("SELECT * FROM sensors_view_dash").fetchone()
        assert row["id"] == 1
        assert row["name"] == "SampleModel"
        assert row["unit"] == "C"
        assert row["location"] == "lab"
        assert row["write_time"] is not None
        assert row["edit_time"] is not None

    def test_multiple_models_union_fields(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.write_object(Alert(level=3, message="high temp"))
        col.create_view("all", SampleModel, Alert)

        rows = conn.execute("SELECT * FROM items_view_all ORDER BY id").fetchall()

        # SampleModel row: has unit/location, NULL for level/message
        assert rows[0]["unit"] == "C"
        assert rows[0]["location"] == "lab"
        assert rows[0]["level"] is None
        assert rows[0]["message"] is None

        # Alert row: has level/message, NULL for unit/location
        assert rows[1]["level"] == 3
        assert rows[1]["message"] == "high temp"
        assert rows[1]["unit"] is None
        assert rows[1]["location"] is None

    def test_view_filters_by_model_name(self, conn):
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.write_object(Alert(level=3, message="high temp"))
        col.create_view("only_alerts", Alert)

        rows = conn.execute("SELECT * FROM items_view_only_alerts").fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "Alert"

    def test_invalid_view_name_raises(self, conn):
        col = Collection(conn, "sensors")
        with pytest.raises(ValueError, match="View name must be"):
            col.create_view("Bad-Name", SampleModel)

    def test_no_models_raises(self, conn):
        col = Collection(conn, "sensors")
        with pytest.raises(ValueError, match="At least one model"):
            col.create_view("empty")


class TestViewCoalesceRenamedFields:
    def test_view_reads_old_json_key_via_coalesce(self, conn):
        """Old rows with the previous field name are readable through the view."""

        # Write a row with the old field name by using the old model
        class SensorV1(CentauroModel):
            __centauro_name__ = "Sensor"
            temp: float = 0.0

        class SensorV2(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", default=0.0)

        col = Collection(conn, "sensors")
        col.write_object(SensorV1(temp=42.0))
        col.write_object(SensorV2(temperature=99.0))

        # Recreate the view with the renamed model
        col.create_view("dash", SensorV2)

        rows = conn.execute(
            "SELECT temperature FROM sensors_view_dash ORDER BY id"
        ).fetchall()
        assert rows[0]["temperature"] == 42.0  # old row, read via COALESCE
        assert rows[1]["temperature"] == 99.0  # new row, read directly

    def test_refresh_view_recreates_columns(self, conn):
        """refresh_view translates renamed columns but preserves the column subset."""

        class SensorA(CentauroModel):
            __centauro_name__ = "Sensor"
            temp: float = 0.0

        class SensorB(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", default=0.0)
            location: str = ""

        col = Collection(conn, "sensors")
        col.write_object(SensorA(temp=1.0))

        col.create_view("dash", SensorA)
        cols_before = [
            r["name"]
            for r in conn.execute("PRAGMA table_info(sensors_view_dash)").fetchall()
        ]
        assert "temp" in cols_before
        assert "temperature" not in cols_before

        # Refresh with updated model — old 'temp' column is translated to
        # 'temperature', but 'location' (a new field) is not added because
        # introspection preserves the existing column subset.
        col.refresh_view("dash", SensorB)
        cols_after = [
            r["name"]
            for r in conn.execute("PRAGMA table_info(sensors_view_dash)").fetchall()
        ]
        assert "temperature" in cols_after
        assert "temp" not in cols_after
        assert "location" not in cols_after

    def test_refresh_view_adds_new_fields_with_explicit_include(self, conn):
        """Explicit include_fields on refresh_view can expand the column subset."""

        class SensorA(CentauroModel):
            __centauro_name__ = "Sensor"
            temp: float = 0.0

        class SensorB(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", default=0.0)
            location: str = ""

        col = Collection(conn, "sensors")
        col.write_object(SensorA(temp=1.0))
        col.create_view("dash", SensorA)

        # Explicitly request all fields including the new one
        col.refresh_view("dash", SensorB, include_fields=["temperature", "location"])
        cols_after = [
            r["name"]
            for r in conn.execute("PRAGMA table_info(sensors_view_dash)").fetchall()
        ]
        assert "temperature" in cols_after
        assert "location" in cols_after
        assert "temp" not in cols_after

    def test_view_raises_if_exists_without_overwrite(self, conn):
        """create_view raises ViewAlreadyExistsError by default."""
        col = Collection(conn, "sensors")
        col.create_view("dash", SampleModel)

        with pytest.raises(ViewAlreadyExistsError, match="overwrite=True"):
            col.create_view("dash", SampleModel)


class TestIncludeFields:
    def test_only_specified_fields_appear_as_columns(self, conn):
        """View only contains the requested subset of model fields."""
        col = Collection(conn, "sensors")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.create_view("lite", SampleModel, include_fields=["unit"])

        row = conn.execute("SELECT * FROM sensors_view_lite").fetchone()
        # 'unit' present, 'location' absent
        assert row["unit"] == "C"
        with pytest.raises(IndexError):
            _ = row["location"]

    def test_include_fields_preserves_model_field_order(self, conn):
        """Columns appear in model-definition order, not include_fields order."""
        col = Collection(conn, "sensors")
        col.write_object(SampleModel(unit="C", location="lab"))
        # Request in reverse order — should still follow model order
        col.create_view("ordered", SampleModel, include_fields=["location", "unit"])

        col_names = [
            r["name"]
            for r in conn.execute("PRAGMA table_info(sensors_view_ordered)").fetchall()
        ]
        unit_idx = col_names.index("unit")
        location_idx = col_names.index("location")
        assert unit_idx < location_idx

    def test_include_fields_unknown_field_raises(self, conn):
        """Requesting a field that doesn't exist on any model raises ValueError."""
        col = Collection(conn, "sensors")
        with pytest.raises(ValueError, match="include_fields"):
            col.create_view("lite", SampleModel, include_fields=["nonexistent"])

    def test_include_fields_renamed_field_uses_coalesce(self, conn):
        """A renamed field in include_fields still reads old JSON keys via COALESCE."""

        class SensorV1(CentauroModel):
            __centauro_name__ = "Sensor"
            temp: float = 0.0
            location: str = ""

        class SensorV2(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", default=0.0)
            location: str = ""

        col = Collection(conn, "sensors")
        col.write_object(SensorV1(temp=42.0, location="lab"))
        col.write_object(SensorV2(temperature=99.0, location="roof"))

        # Only include the renamed field, not location
        col.create_view("lite", SensorV2, include_fields=["temperature"])

        rows = conn.execute(
            "SELECT temperature FROM sensors_view_lite ORDER BY id"
        ).fetchall()
        assert rows[0]["temperature"] == 42.0  # old row read via COALESCE
        assert rows[1]["temperature"] == 99.0  # new row read directly

        # 'location' must not appear as a column
        with pytest.raises(IndexError):
            _ = rows[0]["location"]

    def test_refresh_view_passes_include_fields(self, conn):
        """refresh_view forwards include_fields to create_view."""
        col = Collection(conn, "sensors")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.create_view("lite", SampleModel, include_fields=["unit"])

        # Refresh with same subset — should succeed and keep only 'unit'
        col.refresh_view("lite", SampleModel, include_fields=["unit"])

        row = conn.execute("SELECT * FROM sensors_view_lite").fetchone()
        assert row["unit"] == "C"
        with pytest.raises(IndexError):
            _ = row["location"]

    def test_include_fields_multi_model_union(self, conn):
        """include_fields works across multiple models — picks from the union."""
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.write_object(Alert(level=3, message="critical"))

        # 'level' exists only on Alert, 'unit' only on SampleModel
        col.create_view("mixed", SampleModel, Alert, include_fields=["unit", "level"])

        rows = conn.execute("SELECT * FROM items_view_mixed ORDER BY id").fetchall()
        assert rows[0]["unit"] == "C"
        assert rows[0]["level"] is None
        assert rows[1]["unit"] is None
        assert rows[1]["level"] == 3

        # 'location' and 'message' must not appear
        with pytest.raises(IndexError):
            _ = rows[0]["location"]
        with pytest.raises(IndexError):
            _ = rows[0]["message"]


class TestRefreshViewIntrospection:
    """refresh_view with omitted include_fields introspects the DB."""

    def test_refresh_view_preserves_include_fields_when_omitted(self, conn):
        """Omitting include_fields reuses the columns from the existing view."""
        col = Collection(conn, "sensors")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.create_view("lite", SampleModel, include_fields=["unit"])

        # Refresh without specifying include_fields → should keep only 'unit'
        col.refresh_view("lite", SampleModel)

        cols = [
            r["name"]
            for r in conn.execute("PRAGMA table_info(sensors_view_lite)").fetchall()
        ]
        assert "unit" in cols
        assert "location" not in cols

    def test_refresh_view_explicit_include_fields_overrides(self, conn):
        """Explicit include_fields overrides the introspected columns."""
        col = Collection(conn, "sensors")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.create_view("lite", SampleModel, include_fields=["unit"])

        # Refresh with a different include_fields → should now have both columns
        col.refresh_view("lite", SampleModel, include_fields=["unit", "location"])

        cols = [
            r["name"]
            for r in conn.execute("PRAGMA table_info(sensors_view_lite)").fetchall()
        ]
        assert "unit" in cols
        assert "location" in cols

    def test_refresh_view_nonexistent_view_uses_all_fields(self, conn):
        """Refreshing a view that doesn't exist yet includes all model fields."""
        col = Collection(conn, "sensors")
        col.write_object(SampleModel(unit="C", location="lab"))

        # View doesn't exist yet — include_fields=None should fall back to all fields
        col.refresh_view("fresh", SampleModel)

        cols = [
            r["name"]
            for r in conn.execute("PRAGMA table_info(sensors_view_fresh)").fetchall()
        ]
        assert "unit" in cols
        assert "location" in cols

    def test_refresh_view_preserves_include_fields_only(self, conn):
        """Omitting include_fields preserves the column subset."""
        col = Collection(conn, "sensors")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.create_view("combo", SampleModel, include_fields=["unit"])

        # Refresh with no parameters → should preserve column subset
        col.refresh_view("combo", SampleModel)

        cols = [
            r["name"]
            for r in conn.execute("PRAGMA table_info(sensors_view_combo)").fetchall()
        ]
        assert "unit" in cols
        assert "location" not in cols

    def test_refresh_view_translates_renamed_include_fields(self, conn):
        """Old column names in the view are mapped to current field names."""

        class SensorV1(CentauroModel):
            __centauro_name__ = "Sensor"
            temp: float = 0.0

        class SensorV2(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", default=0.0)

        col = Collection(conn, "sensors")
        col.write_object(SensorV1(temp=1.0))
        col.create_view("dash", SensorV1, include_fields=["temp"])

        # Refresh without include_fields — 'temp' should be translated to
        # 'temperature' via the AliasChoices on SensorV2.
        col.refresh_view("dash", SensorV2)

        cols = [
            r["name"]
            for r in conn.execute("PRAGMA table_info(sensors_view_dash)").fetchall()
        ]
        assert "temperature" in cols
        assert "temp" not in cols

    def test_refresh_view_multi_model_preserves_subset(self, conn):
        """Introspected columns work when multiple models define the view."""
        col = Collection(conn, "items")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.write_object(Alert(level=3, message="critical"))
        col.create_view("mixed", SampleModel, Alert, include_fields=["unit", "level"])

        # Refresh without include_fields — should keep only 'unit' and 'level'
        col.refresh_view("mixed", SampleModel, Alert)

        cols = [
            r["name"]
            for r in conn.execute("PRAGMA table_info(items_view_mixed)").fetchall()
        ]
        assert "unit" in cols
        assert "level" in cols
        assert "location" not in cols
        assert "message" not in cols


class TestDescribeViews:
    def test_no_views(self, conn):
        col = Collection(conn, "sensors")
        result = col.describe_views()
        assert result == {}

    def test_view_returns_columns(self, conn):
        col = Collection(conn, "sensors")
        col.create_view("dash", SampleModel)
        result = col.describe_views()
        assert "sensors_view_dash" in result
        assert "unit" in result["sensors_view_dash"]
        assert "location" in result["sensors_view_dash"]

    def test_view_with_include_fields_returns_subset(self, conn):
        col = Collection(conn, "sensors")
        col.create_view("lite", SampleModel, include_fields=["unit"])
        result = col.describe_views()
        assert "sensors_view_lite" in result
        assert result["sensors_view_lite"] == ["unit"]

    def test_multiple_views(self, conn):
        col = Collection(conn, "sensors")
        col.create_view("dash", SampleModel)
        col.create_view("alerts", Alert)
        result = col.describe_views()
        assert len(result) == 2
        assert "sensors_view_dash" in result
        assert "sensors_view_alerts" in result

    def test_multiple_views_return_their_own_columns(self, conn):
        """Each view reports its own columns."""
        col = Collection(conn, "sensors")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.write_object(Alert(level=3, message="critical"))

        col.create_view("dash", SampleModel)
        col.create_view("alerts", Alert)

        result = col.describe_views()
        assert "unit" in result["sensors_view_dash"]
        assert "location" in result["sensors_view_dash"]
        assert "level" not in result["sensors_view_dash"]

        assert "level" in result["sensors_view_alerts"]
        assert "message" in result["sensors_view_alerts"]
        assert "unit" not in result["sensors_view_alerts"]


class TestSqlSelect:
    @pytest.fixture()
    def col(self, conn):
        col = Collection(conn, "sensors")
        col.write_object(SampleModel(unit="C", location="lab"))
        col.write_object(SampleModel(unit="F", location="roof"))
        col.write_object(SampleModel(unit="C", location="roof"))
        col.write_object(Alert(level=3, message="high temp"))
        col.create_view("dash", SampleModel)
        col.create_view("all", SampleModel, Alert)
        return col

    def test_select_all_rows(self, col):
        df = col.sql_select("SELECT * FROM sensors_view_dash")
        assert len(df) == 3

    def test_where_with_params(self, col):
        df = col.sql_select("SELECT * FROM sensors_view_dash WHERE unit = ?", ("C",))
        assert len(df) == 2
        assert all(u == "C" for u in df["unit"].to_list())

    def test_multiple_conditions(self, col):
        df = col.sql_select(
            "SELECT * FROM sensors_view_dash WHERE unit = ? AND location = ?",
            ("C", "roof"),
        )
        assert len(df) == 1

    def test_aggregation(self, col):
        df = col.sql_select(
            "SELECT unit, COUNT(*) as n FROM sensors_view_dash GROUP BY unit"
        )
        rows = {r["unit"]: r["n"] for r in df.to_dicts()}
        assert rows["C"] == 2
        assert rows["F"] == 1

    def test_cross_model_view(self, col):
        df = col.sql_select("SELECT * FROM sensors_view_all")
        assert len(df) == 4

    def test_non_select_raises(self, col):
        with pytest.raises(ValueError):
            col.sql_select("DELETE FROM sensors_objects")
