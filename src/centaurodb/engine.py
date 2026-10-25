from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from collections.abc import Iterable
from typing import Any, Literal, Protocol, cast, runtime_checkable

from centaurodb.dialect import PostgresDialect, SQLiteDialect

AsyncSafety = Literal["unsafe", "serial", "concurrent"]


@runtime_checkable
class RowProtocol(Protocol):
    """Backend-agnostic row contract for ``Engine.fetchone`` / ``fetchall``.

    SQLite returns ``sqlite3.Row`` (sequence + mapping); Postgres+dict_row
    returns ``dict[str, Any]`` (mapping only). The intersection — and
    therefore the contract every backend must satisfy — is mapping-by-name
    plus ``keys()``. New backends (DuckDB, MySQL, in-memory mocks) only
    have to honour this; no caller may assume positional ``row[0]`` access.

    Always alias projections (``SELECT COUNT(*) AS n``) so callers can
    pull values by name on every backend.
    """

    def __getitem__(self, key: str) -> Any: ...
    def keys(self) -> Iterable[str]: ...


_SELECT_RE = re.compile(r"\s*(SELECT|WITH)\b", re.IGNORECASE)


def _import_polars():
    """Lazy-import polars with a clear error message."""
    try:
        import polars as pl

        return pl
    except ImportError:
        raise ImportError(
            "polars is required for sql_select(). "
            "Install it with: pip install centaurodb[polars]"
        )


def _parse_sqlite_path(url: str) -> str:
    """Extract the file path from a URL string for SQLite.

    Handles:
      ""  / ":memory:" / "sqlite://" / "sqlite://:memory:" → ":memory:"
      "my.db" / "path/to/file.db" (no scheme)               → as-is
      "sqlite:///path/to/file.db"                            → "path/to/file.db"
      "sqlite:///C:/abs/path.db"                             → "C:/abs/path.db"
    """
    if url in ("", ":memory:", "sqlite://", "sqlite://:memory:"):
        return ":memory:"
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    return url


_BACKEND_REGISTRY: dict[str, type["Engine"]] = {}


def register_backend(prefix: str, engine_class: type["Engine"]) -> None:
    """Register a custom ``Engine`` subclass for a URL prefix.

    Third-party backends (DuckDB, MotherDuck, MySQL, in-memory mocks,
    network adapters) plug in here without forking. The matched
    prefix is the part before ``://`` in the connection URL.

    Args:
        prefix: URL scheme without ``://`` (e.g. ``"duckdb"``,
            ``"motherduck"``). Must be non-empty.
        engine_class: A subclass of ``Engine``.

    Raises:
        ValueError: If ``prefix`` is empty.
        TypeError: If ``engine_class`` is not an ``Engine`` subclass.

    Examples::

        from centaurodb import Engine
        from centaurodb.engine import register_backend

        class DuckDBEngine(Engine):
            ...

        register_backend("duckdb", DuckDBEngine)
        e = Engine("duckdb:///path/to/db")  # routes to DuckDBEngine
    """
    if not prefix:
        raise ValueError("prefix must be a non-empty string")
    if not (isinstance(engine_class, type) and issubclass(engine_class, Engine)):
        raise TypeError(
            f"engine_class must be a subclass of Engine, got {engine_class!r}"
        )
    _BACKEND_REGISTRY[prefix] = engine_class


class Engine:
    """Database connection wrapper supporting SQLite and PostgreSQL.

    The backend is chosen automatically from the URL scheme:

    - No argument, ``":memory:"``, plain file path → SQLite
    - ``"sqlite:///path"`` → SQLite
    - ``"postgresql://..."`` or ``"postgres://..."`` → PostgreSQL
    - Any prefix registered via ``register_backend`` → that backend
    - Anything else → SQLite (treated as a file path)

    Args:
        url: Connection URL or file path.  Defaults to in-memory SQLite.
        check_same_thread: SQLite only.  If True (default), enforce single-
            thread access.  Set to False for ``AsyncCollection``.

    Examples::

        Engine()                              # in-memory SQLite
        Engine("my.db")                       # file-based SQLite
        Engine("sqlite:///my.db")             # explicit SQLite URL
        Engine("postgresql://u:p@host/db")    # PostgreSQL
    """

    dialect: SQLiteDialect | PostgresDialect

    def __new__(cls, url: str = ":memory:", **kwargs) -> "Engine":
        # Subclasses constructed directly bypass dispatch.
        if cls is not Engine:
            return object.__new__(cls)
        scheme, sep, _ = url.partition("://")
        if sep:
            target = _BACKEND_REGISTRY.get(scheme)
            if target is not None:
                return object.__new__(target)
        return object.__new__(SQLiteEngine)

    def __init__(self, url: str = ":memory:", **kwargs):
        # Subclasses handle their own init; this is just the signature.
        pass

    @property
    def async_safety(self) -> AsyncSafety:
        """Whether this engine can be used from ``AsyncCollection``.

        - ``"unsafe"``: cannot be dispatched to a worker thread
          (e.g. SQLite with ``check_same_thread=True``).
        - ``"serial"``: safe under ``asyncio.to_thread`` only when
          calls are sequential (current SQLite with
          ``check_same_thread=False``).
        - ``"concurrent"``: safe for parallel calls (future
          pool-backed backends).

        ``AsyncCollection`` raises ``ThreadSafetyError`` for
        ``"unsafe"`` engines and accepts the other two.
        """
        return "unsafe"

    def execute(self, sql: str, params: tuple = ()):
        raise NotImplementedError

    def executemany(self, sql: str, params_seq):
        raise NotImplementedError

    def fetchone(self, sql: str, params: tuple = ()) -> RowProtocol | None:
        raise NotImplementedError

    def fetchall(self, sql: str, params: tuple = ()) -> list[RowProtocol]:
        raise NotImplementedError

    def insert_returning_id(self, sql: str, params: tuple = ()) -> int:
        raise NotImplementedError

    def commit(self):
        raise NotImplementedError

    def rollback(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def sql_select(self, sql: str, params: tuple = ()):
        """Execute a read-only SELECT query and return a Polars DataFrame.

        Only ``SELECT`` and ``WITH`` (CTE) statements are accepted.

        Requires the ``polars`` extra::

            pip install centaurodb[polars]

        Args:
            sql: A SELECT (or WITH … SELECT) SQL string.
            params: Bind parameters for the query.

        Returns:
            A ``polars.DataFrame`` with one column per selected column.

        Raises:
            ValueError: If the statement is not a SELECT / WITH query.
            ImportError: If polars is not installed.

        Examples:
            >>> df = conn.sql_select("SELECT id, name FROM items_objects")
            >>> df.shape
            (3, 2)
        """
        pl = _import_polars()
        if not _SELECT_RE.match(sql):
            raise ValueError(
                "sql_select() only accepts SELECT or WITH statements. "
                "Use sql_execute() for mutations."
            )
        rows = self.fetchall(sql, params)
        return self._rows_to_dataframe(rows, pl)

    def sql_execute(self, sql: str, params: tuple = ()) -> int:
        """Execute an arbitrary SQL statement and return affected row count.

        Commits the transaction on success.  Use this for INSERT,
        UPDATE, DELETE, DDL, or any non-SELECT statement.

        Args:
            sql: Any SQL statement.
            params: Bind parameters.

        Returns:
            The number of rows affected (0 for DDL).

        Examples:
            >>> n = conn.sql_execute(
            ...     "UPDATE items_objects SET meta = ? WHERE id = ?",
            ...     ('{"unit": "F"}', 1),
            ... )
            >>> n
            1
        """
        cursor = self.execute(sql, params)
        self.commit()
        rc = cursor.rowcount if hasattr(cursor, "rowcount") else 0
        return max(rc, 0)

    def _rows_to_dataframe(self, rows: list, pl):
        """Convert database rows to a Polars DataFrame."""
        raise NotImplementedError

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class SQLiteEngine(Engine):
    """SQLite backend via the stdlib ``sqlite3`` module."""

    def __init__(self, url: str = ":memory:", *, check_same_thread: bool = True):
        self.dialect = SQLiteDialect()
        self._check_same_thread = check_same_thread
        path = _parse_sqlite_path(url)
        self._conn = sqlite3.connect(path, check_same_thread=check_same_thread)
        self._conn.row_factory = sqlite3.Row
        for stmt in self.dialect.setup_statements(path):
            self._conn.execute(stmt)

    @property
    def raw(self) -> sqlite3.Connection:
        """Return the underlying sqlite3.Connection object."""
        return self._conn

    @property
    def async_safety(self) -> AsyncSafety:
        return "serial" if not self._check_same_thread else "unsafe"

    def execute(self, sql: str, params: tuple = ()):
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, params_seq):
        return self._conn.executemany(sql, params_seq)

    def fetchone(self, sql: str, params: tuple = ()) -> RowProtocol | None:
        return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[RowProtocol]:
        return self._conn.execute(sql, params).fetchall()

    def insert_returning_id(self, sql: str, params: tuple = ()) -> int:
        cursor = self._conn.execute(sql, params)
        # SQLite always sets lastrowid after a successful INSERT into a
        # table with a rowid (all our tables have one).
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def _rows_to_dataframe(self, rows: list, pl):
        if not rows:
            return pl.DataFrame()
        keys = rows[0].keys()
        return pl.DataFrame([dict(zip(keys, row)) for row in rows])

    def close(self):
        self._conn.close()


class PostgresEngine(Engine):
    """PostgreSQL backend via ``psycopg``.

    The ``# type: ignore[arg-type]`` markers in this class are not bug
    suppressions — they paper over psycopg's type stubs, which model
    ``query`` as a strict ``Query`` / ``QueryNoTemplate`` newtype and
    ``row_factory`` as ``RowFactory[TupleRow]`` even though plain ``str``
    and ``dict_row`` are the documented runtime API.
    """

    def __init__(self, url: str = "", **kwargs):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:
            raise ImportError(
                "psycopg is required for PostgreSQL support. "
                "Install it with: pip install centaurodb[postgres]"
            )
        self.dialect = PostgresDialect()
        self._check_same_thread = False
        self._conn = psycopg.connect(
            url,
            row_factory=dict_row,  # type: ignore[arg-type]
            autocommit=False,
        )

    @property
    def raw(self):
        """Return the underlying psycopg connection."""
        return self._conn

    @property
    def async_safety(self) -> AsyncSafety:
        # Today PostgresEngine wraps a single sync psycopg connection,
        # so AsyncCollection must serialise calls. When a pool-backed
        # async variant lands this should report "concurrent".
        return "serial"

    def execute(self, sql: str, params: tuple = ()):
        return self._conn.execute(sql, params)  # type: ignore[arg-type]

    def executemany(self, sql: str, params_seq):
        with self._conn.cursor() as cursor:
            cursor.executemany(sql, params_seq)  # type: ignore[arg-type]

    def fetchone(self, sql: str, params: tuple = ()) -> RowProtocol | None:
        return self._conn.execute(sql, params).fetchone()  # type: ignore[arg-type,return-value]

    def fetchall(self, sql: str, params: tuple = ()) -> list[RowProtocol]:
        return self._conn.execute(sql, params).fetchall()  # type: ignore[arg-type,return-value]

    def insert_returning_id(self, sql: str, params: tuple = ()) -> int:
        row = self._conn.execute(sql, params).fetchone()  # type: ignore[arg-type]
        # dict_row factory always returns dict[str, Any] | None on a SELECT
        # that yielded a row; INSERT … RETURNING id always yields one.
        # The cast is needed because psycopg's stubs default fetchone() to
        # the strict tuple-row Row type even when row_factory=dict_row.
        assert row is not None
        return cast("dict[str, Any]", row)["id"]

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def _rows_to_dataframe(self, rows: list, pl):
        if not rows:
            return pl.DataFrame()
        return pl.DataFrame(rows)

    def close(self):
        self._conn.close()


def _libsql_param(v):
    """libsql rejects datetime/date; mirror sqlite3's default adapters."""
    if isinstance(v, datetime):
        return v.isoformat(" ")
    if isinstance(v, date):
        return v.isoformat()
    return v


def _libsql_params(params):
    return tuple(_libsql_param(v) for v in params)


class _LibSQLRow(tuple):
    """Tuple row that also resolves column names, like ``sqlite3.Row``."""

    _names: tuple[str, ...]

    def __new__(cls, values, names):
        row = super().__new__(cls, values)
        row._names = tuple(names)
        return row

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._names.index(key))
            except ValueError:
                raise IndexError("No item with that key") from None
        return tuple.__getitem__(self, key)

    def __getnewargs__(self):
        return (tuple(self), self._names)

    def keys(self) -> list[str]:
        return list(self._names)


class _LibSQLCursor:
    """Wraps a libsql cursor so fetched rows are ``_LibSQLRow``."""

    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    def _names(self) -> list[str]:
        return [d[0] for d in self._cursor.description or ()]

    def fetchone(self):
        row = self._cursor.fetchone()
        return None if row is None else _LibSQLRow(row, self._names())

    def fetchall(self) -> list[Any]:
        names = self._names()
        return [_LibSQLRow(r, names) for r in self._cursor.fetchall()]


class LibSQLEngine(Engine):
    """libSQL backend via the ``libsql`` package.

    A plain path opens a local file with the SQLite setup; a ``libsql://``
    URL opens a remote database and skips file-only pragmas (WAL).
    """

    def __init__(self, url: str = ":memory:", *, auth_token: str | None = None):
        try:
            import libsql  # pyright: ignore[reportMissingImports]
        except ImportError:
            raise ImportError(
                "libsql is required for libSQL support. "
                "Install it with: pip install centaurodb[libsql]"
            )
        self.dialect = SQLiteDialect()
        self._check_same_thread = True
        connect = cast(Any, libsql).connect
        if auth_token:
            self._conn = connect(url, auth_token=auth_token)
        else:
            self._conn = connect(url)
        if "://" in url:
            self._conn.execute("PRAGMA foreign_keys=ON")
        else:
            for stmt in self.dialect.setup_statements(url):
                self._conn.execute(stmt)

    @property
    def raw(self):
        """Return the underlying libsql connection."""
        return self._conn

    @property
    def async_safety(self) -> AsyncSafety:
        return "unsafe"

    def execute(self, sql: str, params: tuple = ()):
        return _LibSQLCursor(self._conn.execute(sql, _libsql_params(params)))

    def executemany(self, sql: str, params_seq):
        return _LibSQLCursor(
            self._conn.executemany(sql, [_libsql_params(p) for p in params_seq])
        )

    def fetchone(self, sql: str, params: tuple = ()) -> RowProtocol | None:
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[RowProtocol]:
        return self.execute(sql, params).fetchall()

    def insert_returning_id(self, sql: str, params: tuple = ()) -> int:
        cursor = self._conn.execute(sql, _libsql_params(params))
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def _rows_to_dataframe(self, rows: list, pl):
        if not rows:
            return pl.DataFrame()
        keys = rows[0].keys()
        return pl.DataFrame([dict(zip(keys, row)) for row in rows])

    def close(self):
        self._conn.close()


# Built-in backend registrations. Custom backends call register_backend()
# at import time to plug into the same dispatch.
register_backend("postgresql", PostgresEngine)
register_backend("postgres", PostgresEngine)
register_backend("sqlite", SQLiteEngine)
register_backend("libsql", LibSQLEngine)
