import sqlite3
from unittest.mock import patch

import polars as pl
import pytest

from centaurodb import Engine, Collection, CentauroModel
from centaurodb.engine import SQLiteEngine, PostgresEngine, _parse_sqlite_path


class TestURLParsing:
    def test_no_args_returns_sqlite(self):
        conn = Engine()
        assert isinstance(conn, SQLiteEngine)
        conn.close()

    def test_memory_returns_sqlite(self):
        conn = Engine(":memory:")
        assert isinstance(conn, SQLiteEngine)
        conn.close()

    def test_file_path_returns_sqlite(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = Engine(db_path)
        assert isinstance(conn, SQLiteEngine)
        conn.close()

    def test_sqlite_url_returns_sqlite(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = Engine(f"sqlite:///{db_path}")
        assert isinstance(conn, SQLiteEngine)
        conn.close()

    def test_sqlite_memory_url(self):
        conn = Engine("sqlite://")
        assert isinstance(conn, SQLiteEngine)
        conn.close()

    def test_sqlite_memory_url_explicit(self):
        conn = Engine("sqlite://:memory:")
        assert isinstance(conn, SQLiteEngine)
        conn.close()

    def test_postgresql_url_raises_without_psycopg(self):
        with patch.dict("sys.modules", {"psycopg": None, "psycopg.rows": None}):
            with pytest.raises(ImportError, match="psycopg"):
                Engine("postgresql://user:pass@localhost/db")

    def test_postgres_url_raises_without_psycopg(self):
        with patch.dict("sys.modules", {"psycopg": None, "psycopg.rows": None}):
            with pytest.raises(ImportError, match="psycopg"):
                Engine("postgres://user:pass@localhost/db")

    def test_postgresql_url_creates_postgres_engine(self):
        """Engine('postgresql://...') dispatches to PostgresEngine."""
        conn = Engine.__new__(Engine, "postgresql://user:pass@host/db")
        assert isinstance(conn, PostgresEngine)

    def test_postgres_url_creates_postgres_engine(self):
        """Engine('postgres://...') dispatches to PostgresEngine."""
        conn = Engine.__new__(Engine, "postgres://user:pass@host/db")
        assert isinstance(conn, PostgresEngine)


class TestParseSqlitePath:
    def test_empty_string(self):
        assert _parse_sqlite_path("") == ":memory:"

    def test_memory(self):
        assert _parse_sqlite_path(":memory:") == ":memory:"

    def test_sqlite_scheme_no_path(self):
        assert _parse_sqlite_path("sqlite://") == ":memory:"

    def test_sqlite_scheme_memory(self):
        assert _parse_sqlite_path("sqlite://:memory:") == ":memory:"

    def test_sqlite_scheme_file(self):
        assert _parse_sqlite_path("sqlite:///my.db") == "my.db"

    def test_sqlite_scheme_abs_path(self):
        assert _parse_sqlite_path("sqlite:///tmp/data.db") == "tmp/data.db"

    def test_plain_file(self):
        assert _parse_sqlite_path("my.db") == "my.db"

    def test_relative_path(self):
        assert _parse_sqlite_path("path/to/file.db") == "path/to/file.db"


class TestEngineInit:
    def test_default_in_memory(self):
        conn = Engine()
        result = conn.execute("SELECT 1").fetchone()
        assert result[0] == 1
        conn.close()

    def test_file_based(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = Engine(db_path)
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        conn.close()
        assert (tmp_path / "test.db").exists()

    def test_wal_mode_enabled_for_file(self, tmp_path):
        conn = Engine(str(tmp_path / "test.db"))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_wal_mode_skipped_for_memory(self):
        conn = Engine()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "memory"
        conn.close()

    def test_foreign_keys_enabled(self):
        conn = Engine()
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        conn.close()

    def test_row_factory_is_row(self):
        conn = Engine()
        conn.execute("CREATE TABLE t (name TEXT)")
        conn.execute("INSERT INTO t VALUES ('alice')")
        row = conn.execute("SELECT name FROM t").fetchone()
        assert row["name"] == "alice"
        conn.close()

    def test_has_dialect(self):
        conn = Engine()
        from centaurodb.dialect import SQLiteDialect

        assert isinstance(conn.dialect, SQLiteDialect)
        conn.close()


class TestEngineMethods:
    def test_execute_returns_cursor(self):
        conn = Engine()
        cursor = conn.execute("SELECT 1")
        assert isinstance(cursor, sqlite3.Cursor)
        conn.close()

    def test_executemany(self):
        conn = Engine()
        conn.execute("CREATE TABLE t (val INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
        conn.commit()
        rows = conn.execute("SELECT val FROM t ORDER BY val").fetchall()
        assert [r[0] for r in rows] == [1, 2, 3]
        conn.close()

    def test_commit_persists_data(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = Engine(db_path)
        conn.execute("CREATE TABLE t (val INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
        conn.commit()
        conn.close()

        conn2 = Engine(db_path)
        row = conn2.execute("SELECT val FROM t").fetchone()
        assert row[0] == 42
        conn2.close()

    def test_raw_returns_sqlite3_connection(self):
        conn = Engine()
        assert isinstance(conn.raw, sqlite3.Connection)
        conn.close()

    def test_fetchone(self):
        conn = Engine()
        conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'alice')")
        row = conn.fetchone("SELECT * FROM t WHERE id = ?", (1,))
        assert row["name"] == "alice"
        conn.close()

    def test_fetchone_returns_none(self):
        conn = Engine()
        conn.execute("CREATE TABLE t (id INTEGER)")
        row = conn.fetchone("SELECT * FROM t WHERE id = ?", (999,))
        assert row is None
        conn.close()

    def test_fetchall(self):
        conn = Engine()
        conn.execute("CREATE TABLE t (val INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
        rows = conn.fetchall("SELECT val FROM t ORDER BY val")
        assert [r[0] for r in rows] == [1, 2, 3]
        conn.close()

    def test_insert_returning_id(self):
        conn = Engine()
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
        row_id = conn.insert_returning_id("INSERT INTO t (name) VALUES (?)", ("alice",))
        assert row_id == 1
        row_id2 = conn.insert_returning_id("INSERT INTO t (name) VALUES (?)", ("bob",))
        assert row_id2 == 2
        conn.close()


class TestEngineContextManager:
    def test_context_manager(self):
        with Engine() as conn:
            conn.execute("SELECT 1")

    def test_context_manager_closes_connection(self):
        with Engine() as conn:
            pass
        with pytest.raises(Exception):
            conn.execute("SELECT 1")


class TestEngineBackwardCompat:
    """Ensure old calling conventions still work."""

    def test_positional_path_arg(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = Engine(db_path)
        assert isinstance(conn, SQLiteEngine)
        conn.close()

    def test_check_same_thread_kwarg(self):
        conn = Engine(":memory:", check_same_thread=False)
        assert isinstance(conn, SQLiteEngine)
        assert conn._check_same_thread is False
        conn.close()

    def test_isinstance_engine(self):
        conn = Engine()
        assert isinstance(conn, Engine)
        assert isinstance(conn, SQLiteEngine)
        conn.close()


class _Item(CentauroModel):
    __centauro_name__ = "Item"
    color: str = ""
    price: float = 0.0


class TestSqlSelect:
    def test_returns_dataframe(self, conn):
        col = Collection(conn, "store")
        col.write_object(_Item(color="red", price=9.99))
        col.write_object(_Item(color="blue", price=4.50))

        df = conn.sql_select(f"SELECT id, name, meta FROM {col._objects_table}")
        assert isinstance(df, pl.DataFrame)
        assert df.shape == (2, 3)
        assert df["id"].to_list() == [1, 2]

    def test_with_params(self, conn):
        col = Collection(conn, "store")
        col.write_object(_Item(color="red"))
        col.write_object(_Item(color="blue"))

        df = conn.sql_select(
            f"SELECT id FROM {col._objects_table} WHERE name = ?",
            ("Item",),
        )
        assert df.shape == (2, 1)

    def test_empty_result(self, conn):
        col = Collection(conn, "store")
        df = conn.sql_select(f"SELECT * FROM {col._objects_table}")
        assert isinstance(df, pl.DataFrame)
        assert df.is_empty()

    def test_with_cte(self, conn):
        col = Collection(conn, "store")
        col.write_object(_Item(color="red"))

        df = conn.sql_select(
            f"WITH items AS (SELECT * FROM {col._objects_table}) SELECT id FROM items"
        )
        assert df.shape == (1, 1)

    def test_rejects_insert(self, conn):
        Collection(conn, "store")
        with pytest.raises(ValueError, match="SELECT or WITH"):
            conn.sql_select("INSERT INTO store_objects (name) VALUES ('x')")

    def test_rejects_update(self, conn):
        Collection(conn, "store")
        with pytest.raises(ValueError, match="SELECT or WITH"):
            conn.sql_select("UPDATE store_objects SET name = 'x'")

    def test_rejects_delete(self, conn):
        Collection(conn, "store")
        with pytest.raises(ValueError, match="SELECT or WITH"):
            conn.sql_select("DELETE FROM store_objects")

    def test_rejects_drop(self, conn):
        with pytest.raises(ValueError, match="SELECT or WITH"):
            conn.sql_select("DROP TABLE IF EXISTS foo")

    def test_view_query(self, conn):
        col = Collection(conn, "store")
        col.write_object(_Item(color="red", price=9.99))
        col.create_view("all", _Item)

        df = conn.sql_select("SELECT color, price FROM store_view_all")
        assert df.shape == (1, 2)
        assert df["color"].to_list() == ["red"]


class TestSqlExecute:
    def test_update_returns_rowcount(self, conn):
        col = Collection(conn, "store")
        col.write_object(_Item(color="red"))
        col.write_object(_Item(color="blue"))

        n = conn.sql_execute(
            f"UPDATE {col._objects_table} SET meta = ? WHERE id = ?",
            ('{"color": "green", "price": 0.0}', 1),
        )
        assert n == 1

        obj = col.read_object_by_id(1, _Item)
        assert obj.color == "green"

    def test_delete_returns_rowcount(self, conn):
        col = Collection(conn, "store")
        col.write_object(_Item(color="red"))
        col.write_object(_Item(color="blue"))

        n = conn.sql_execute(f"DELETE FROM {col._objects_table} WHERE id = ?", (1,))
        assert n == 1

    def test_ddl_returns_zero(self, conn):
        n = conn.sql_execute(
            "CREATE TABLE IF NOT EXISTS scratch (id INTEGER PRIMARY KEY)"
        )
        assert n == 0

    def test_commits_automatically(self, conn):
        col = Collection(conn, "store")
        col.write_object(_Item(color="red"))

        conn.sql_execute(
            f"UPDATE {col._objects_table} SET meta = ? WHERE id = ?",
            ('{"color": "green", "price": 0.0}', 1),
        )
        # Verify committed by reading through a fresh connection
        obj = col.read_object_by_id(1, _Item)
        assert obj.color == "green"
