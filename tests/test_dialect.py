from centaurodb.dialect import SQLiteDialect, PostgresDialect


class TestSQLiteDialect:
    def setup_method(self):
        self.d = SQLiteDialect()

    def test_placeholder(self):
        assert self.d.ph == "?"

    def test_create_objects_table(self):
        sql = self.d.create_objects_table("my_objects")
        assert "my_objects" in sql
        assert "INTEGER PRIMARY KEY AUTOINCREMENT" in sql
        assert "JSON" in sql
        assert "DATETIME" in sql

    def test_create_objects_index(self):
        sql = self.d.create_objects_index("my", "my_objects")
        assert "idx_my_name" in sql
        assert "my_objects" in sql

    def test_create_values_table(self):
        sql = self.d.create_values_table("my_values", "my_objects")
        assert "my_values" in sql
        assert "REFERENCES my_objects(id)" in sql
        assert "PRIMARY KEY (data_object_id, time)" in sql

    def test_insert_object(self):
        sql = self.d.insert_object("my_objects")
        assert "INSERT INTO my_objects" in sql
        assert "json(?)" in sql
        assert "RETURNING" not in sql

    def test_update_object(self):
        sql = self.d.update_object("my_objects")
        assert "UPDATE my_objects" in sql
        assert "json(?)" in sql

    def test_select_object(self):
        sql = self.d.select_object("my_objects")
        assert "SELECT * FROM my_objects WHERE id = ?" in sql

    def test_insert_values(self):
        sql = self.d.insert_values("my_values")
        assert "INSERT INTO my_values" in sql
        assert sql.count("?") == 4

    def test_insert_or_ignore(self):
        sql = self.d.insert_or_ignore_values("my_values")
        assert "INSERT OR IGNORE" in sql

    def test_upsert_values(self):
        sql = self.d.upsert_values("my_values")
        assert "INSERT OR REPLACE" in sql

    def test_delete_values(self):
        sql = self.d.delete_values("my_values")
        assert "DELETE FROM my_values" in sql

    def test_select_values(self):
        sql = self.d.select_values("my_values")
        assert "ORDER BY time" in sql

    def test_json_col(self):
        expr = self.d.json_col("unit")
        assert "json_extract(meta, '$.unit')" in expr
        assert "AS unit" in expr

    def test_coalesce_json_cols(self):
        expr = self.d.coalesce_json_cols("temp", ["temp", "temperature"])
        assert "COALESCE" in expr
        assert "json_extract(meta, '$.temp')" in expr
        assert "json_extract(meta, '$.temperature')" in expr
        assert "AS temp" in expr

    def test_create_expression_index(self):
        sql = self.d.create_expression_index("my", "my_objects", "unit")
        assert "idx_my_unit" in sql
        assert "json_extract(meta, '$.unit')" in sql

    def test_create_plain_index(self):
        sql = self.d.create_plain_index("my", "my_objects", "write_time")
        assert "idx_my_write_time" in sql
        assert "my_objects" in sql
        assert "write_time" in sql
        assert "json_extract" not in sql
        assert "IF NOT EXISTS" in sql

    def test_drop_index(self):
        sql = self.d.drop_index("idx_my_status")
        assert "DROP INDEX" in sql
        assert "idx_my_status" in sql
        assert "IF EXISTS" in sql

    def test_check_view_exists(self):
        sql = self.d.check_view_exists()
        assert "sqlite_master" in sql

    def test_list_views(self):
        sql = self.d.list_views()
        assert "sqlite_master" in sql
        assert "LIKE" in sql

    def test_list_indexes(self):
        sql = self.d.list_indexes()
        assert "sqlite_master" in sql

    def test_drop_view(self):
        assert self.d.drop_view("v") == "DROP VIEW v"

    def test_create_view(self):
        sql = self.d.create_view("v", "t", "col1, col2", "'M1'")
        assert "CREATE VIEW v AS" in sql
        assert "col1, col2" in sql
        assert "FROM t" in sql
        assert "WHERE name IN ('M1')" in sql

    def test_list_view_columns(self):
        sql = self.d.list_view_columns("my_view")
        assert "PRAGMA table_info(my_view)" in sql

    def test_delete_object(self):
        sql = self.d.delete_object("my_objects")
        assert sql == "DELETE FROM my_objects WHERE id = ?"

    def test_delete_all_objects(self):
        sql = self.d.delete_all_objects("my_objects")
        assert sql == "DELETE FROM my_objects WHERE name = ?"

    def test_delete_objects_by_query(self):
        from centaurodb.query import Condition

        c = Condition(field="status", op="eq", value="active")
        sql, params = self.d.delete_objects_by_query("my_objects", "Sensor", [c])
        assert "DELETE FROM my_objects WHERE" in sql
        assert "name = ?" in sql
        assert "json_extract(meta, '$.status') = ?" in sql
        assert params == ["Sensor", "active"]

    def test_select_latest_object(self):
        sql = self.d.select_latest_object("my_objects")
        assert "SELECT * FROM my_objects" in sql
        assert "WHERE name = ?" in sql
        assert "ORDER BY edit_time DESC, id DESC" in sql
        assert "LIMIT 1" in sql
        assert "%s" not in sql

    def test_setup_statements_memory(self):
        stmts = self.d.setup_statements(":memory:")
        assert "PRAGMA foreign_keys=ON" in stmts
        assert "PRAGMA journal_mode=WAL" not in stmts

    def test_setup_statements_file(self):
        stmts = self.d.setup_statements("my.db")
        assert "PRAGMA journal_mode=WAL" in stmts
        assert "PRAGMA foreign_keys=ON" in stmts


class TestPostgresDialect:
    def setup_method(self):
        self.d = PostgresDialect()

    def test_placeholder(self):
        assert self.d.ph == "%s"

    def test_create_objects_table(self):
        sql = self.d.create_objects_table("my_objects")
        assert "SERIAL PRIMARY KEY" in sql
        assert "JSONB" in sql
        assert "TIMESTAMPTZ" in sql

    def test_create_values_table(self):
        sql = self.d.create_values_table("my_values", "my_objects")
        assert "DOUBLE PRECISION" in sql
        assert "TIMESTAMPTZ" in sql

    def test_insert_object_has_returning(self):
        sql = self.d.insert_object("my_objects")
        assert "RETURNING id" in sql
        assert "::jsonb" in sql

    def test_update_object(self):
        sql = self.d.update_object("my_objects")
        assert "::jsonb" in sql

    def test_select_object(self):
        sql = self.d.select_object("my_objects")
        assert "WHERE id = %s" in sql

    def test_insert_or_ignore(self):
        sql = self.d.insert_or_ignore_values("my_values")
        assert "ON CONFLICT DO NOTHING" in sql

    def test_upsert_values(self):
        sql = self.d.upsert_values("my_values")
        assert "ON CONFLICT (data_object_id, time)" in sql
        assert "DO UPDATE SET" in sql

    def test_json_col(self):
        expr = self.d.json_col("unit")
        assert "meta->>'unit'" in expr
        assert "AS unit" in expr

    def test_coalesce_json_cols(self):
        expr = self.d.coalesce_json_cols("temp", ["temp", "temperature"])
        assert "COALESCE" in expr
        assert "meta->>'temp'" in expr
        assert "meta->>'temperature'" in expr

    def test_create_expression_index(self):
        sql = self.d.create_expression_index("my", "my_objects", "unit")
        assert "((meta->>'unit'))" in sql

    def test_create_plain_index(self):
        sql = self.d.create_plain_index("my", "my_objects", "write_time")
        assert "idx_my_write_time" in sql
        assert "my_objects" in sql
        assert "write_time" in sql
        assert "meta->>" not in sql
        assert "IF NOT EXISTS" in sql

    def test_drop_index(self):
        sql = self.d.drop_index("idx_my_status")
        assert "DROP INDEX" in sql
        assert "idx_my_status" in sql
        assert "IF EXISTS" in sql

    def test_check_view_exists(self):
        sql = self.d.check_view_exists()
        assert "information_schema.views" in sql

    def test_list_views(self):
        sql = self.d.list_views()
        assert "information_schema.views" in sql

    def test_list_indexes(self):
        sql = self.d.list_indexes()
        assert "pg_indexes" in sql

    def test_list_view_columns(self):
        sql = self.d.list_view_columns("my_view")
        assert "information_schema.columns" in sql
        assert "column_name AS name" in sql
        assert "%s" in sql
        assert "ORDER BY ordinal_position" in sql

    def test_delete_object(self):
        sql = self.d.delete_object("my_objects")
        assert sql == "DELETE FROM my_objects WHERE id = %s"

    def test_delete_all_objects(self):
        sql = self.d.delete_all_objects("my_objects")
        assert sql == "DELETE FROM my_objects WHERE name = %s"

    def test_delete_objects_by_query(self):
        from centaurodb.query import Condition

        c = Condition(field="status", op="eq", value="active")
        sql, params = self.d.delete_objects_by_query("my_objects", "Sensor", [c])
        assert "DELETE FROM my_objects WHERE" in sql
        assert "name = %s" in sql
        assert "meta->>'status' = %s" in sql
        assert params == ["Sensor", "active"]

    def test_select_latest_object(self):
        sql = self.d.select_latest_object("my_objects")
        assert "SELECT * FROM my_objects" in sql
        assert "WHERE name = %s" in sql
        assert "ORDER BY edit_time DESC, id DESC" in sql
        assert "LIMIT 1" in sql
        assert "?" not in sql

    def test_setup_statements_empty(self):
        assert self.d.setup_statements("") == []

    def test_placeholder_consistency(self):
        """All generated SQL should use %s, never ?."""
        methods = [
            self.d.insert_object("t"),
            self.d.update_object("t"),
            self.d.select_object("t"),
            self.d.select_latest_object("t"),
            self.d.insert_values("t"),
            self.d.insert_or_ignore_values("t"),
            self.d.upsert_values("t"),
            self.d.delete_values("t"),
            self.d.delete_object("t"),
            self.d.delete_all_objects("t"),
            self.d.select_values("t"),
            self.d.check_view_exists(),
            self.d.list_views(),
            self.d.list_indexes(),
            self.d.create_plain_index("t", "t_objects", "field"),
            self.d.drop_index("idx_t_field"),
        ]
        for sql in methods:
            assert "?" not in sql, f"Found '?' in: {sql}"


class TestSQLitePlaceholderConsistency:
    def test_all_methods_use_question_mark(self):
        d = SQLiteDialect()
        methods = [
            d.insert_object("t"),
            d.update_object("t"),
            d.select_object("t"),
            d.select_latest_object("t"),
            d.insert_values("t"),
            d.insert_or_ignore_values("t"),
            d.upsert_values("t"),
            d.delete_values("t"),
            d.delete_object("t"),
            d.delete_all_objects("t"),
            d.select_values("t"),
            d.check_view_exists(),
            d.list_views(),
            d.list_indexes(),
            d.create_plain_index("t", "t_objects", "field"),
            d.drop_index("idx_t_field"),
        ]
        for sql in methods:
            assert "%s" not in sql, f"Found '%s' in: {sql}"
