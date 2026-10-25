import sys

import pytest

libsql = pytest.importorskip("libsql")

from centaurodb.engine import (  # noqa: E402
    Engine,
    LibSQLEngine,
    RowProtocol,
    SQLiteEngine,
)


def test_libsql_scheme_routes_to_libsql_engine_with_the_token(monkeypatch):
    calls = []

    class _FakeConn:
        def execute(self, sql, params=()):
            calls.append(sql)

        def close(self):
            pass

    monkeypatch.setattr(
        libsql, "connect", lambda url, **kw: (calls.append((url, kw)), _FakeConn())[1]
    )
    e = Engine("libsql://db.example.com", auth_token="tok")
    assert isinstance(e, LibSQLEngine)
    assert calls[0] == ("libsql://db.example.com", {"auth_token": "tok"})
    assert not any("journal_mode" in c for c in calls[1:] if isinstance(c, str))


def test_plain_path_still_routes_to_sqlite(tmp_path):
    e = Engine(str(tmp_path / "x.db"))
    assert isinstance(e, SQLiteEngine)
    e.close()


def test_local_file_runs_the_file_setup(tmp_path):
    e = LibSQLEngine(str(tmp_path / "x.db"))
    assert e.fetchone("PRAGMA journal_mode")["journal_mode"] == "wal"
    e.close()


def test_rows_are_mappings_and_sequences(tmp_path):
    e = LibSQLEngine(str(tmp_path / "x.db"))
    row = e.fetchone("SELECT 1 AS n, 'a' AS s")
    assert row["n"] == 1 and row[1] == "a" and list(row.keys()) == ["n", "s"]
    assert isinstance(row, RowProtocol)
    assert e.execute("SELECT 2 AS n").fetchall()[0]["n"] == 2
    e.close()


def test_insert_returning_id_uses_lastrowid(tmp_path):
    e = LibSQLEngine(str(tmp_path / "x.db"))
    e.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    assert e.insert_returning_id("INSERT INTO t (v) VALUES (?)", ("a",)) == 1
    assert e.insert_returning_id("INSERT INTO t (v) VALUES (?)", ("b",)) == 2
    e.commit()
    e.close()


def test_rollback_discards_and_executemany_inserts(tmp_path):
    e = LibSQLEngine(str(tmp_path / "x.db"))
    e.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    e.commit()
    e.executemany("INSERT INTO t (v) VALUES (?)", iter([("a",), ("b",)]))
    e.rollback()
    assert e.fetchone("SELECT COUNT(*) AS n FROM t")["n"] == 0
    e.executemany("INSERT INTO t (v) VALUES (?)", iter([("a",), ("b",)]))
    e.commit()
    assert e.fetchone("SELECT COUNT(*) AS n FROM t")["n"] == 2
    e.close()


def test_sql_select_returns_a_dataframe(tmp_path):
    pytest.importorskip("polars")
    e = LibSQLEngine(str(tmp_path / "x.db"))
    df = e.sql_select("SELECT 1 AS n")
    assert df.columns == ["n"] and df.shape == (1, 1)
    e.close()


def test_missing_package_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "libsql", None)
    with pytest.raises(ImportError, match=r"centaurodb\[libsql\]"):
        LibSQLEngine(":memory:")


def test_async_safety_is_unsafe(tmp_path):
    assert LibSQLEngine(str(tmp_path / "x.db")).async_safety == "unsafe"
