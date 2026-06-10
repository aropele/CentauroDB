from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from centaurodb.query import Condition, OrGroup

_OP_MAP = {
    "eq": "=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "like": "LIKE",
    "in": "IN",
}

_STANDARD_COLS = {"id", "write_time", "edit_time"}


class _QueryMixin:
    """Shared query-building logic for both dialects.

    Subclasses must define ``ph``, ``_json_field_expr``, and optionally
    override ``_numeric_cast``.
    """

    ph: str

    def _json_field_expr(self, field: str) -> str:
        """Return the SQL expression to extract a JSON field from meta."""
        raise NotImplementedError

    def _numeric_cast(self, expr: str) -> str:
        """Wrap an expression in a numeric cast if needed by the backend."""
        return expr

    def _aggregate_cast(self, expr: str, field_type: type) -> str:
        """Wrap an expression in the correct cast for aggregate comparisons."""
        return expr

    def _aggregate_field_expr(self, field: str, field_type: type) -> str:
        if field in _STANDARD_COLS:
            base_expr = field
        else:
            base_expr = self._json_field_expr(field)
        return self._aggregate_cast(base_expr, field_type)

    def _build_where(
        self, target_name: str, conditions: list[Condition | OrGroup]
    ) -> tuple[str, list[Any]]:
        from centaurodb.query import OrGroup

        clauses = [f"name = {self.ph}"]
        params: list[Any] = [target_name]

        for item in conditions:
            if isinstance(item, OrGroup):
                or_parts: list[str] = []
                or_params: list[Any] = []
                for c in item.conditions:
                    frag, p = self._condition_clause(c)
                    or_parts.append(frag)
                    or_params.extend(p)
                clauses.append(f"({' OR '.join(or_parts)})")
                params.extend(or_params)
            else:
                frag, p = self._condition_clause(item)
                clauses.append(frag)
                params.extend(p)

        return " AND ".join(clauses), params

    def _condition_clause(self, c: Condition) -> tuple[str, list[Any]]:
        """Return ``(sql_fragment, params)`` for a single Condition."""
        op_sql = _OP_MAP[c.op]
        if c.field in _STANDARD_COLS:
            field_expr = c.field
        else:
            field_expr = self._json_field_expr(c.field)
            if isinstance(c.value, (int, float)) and c.op not in (
                "like",
                "in",
            ):
                field_expr = self._numeric_cast(field_expr)

        if c.op == "in":
            if not isinstance(c.value, (list, tuple)):
                raise ValueError(
                    f"Value for 'in' operator must be a list or tuple, got {type(c.value)}"
                )
            placeholders = ", ".join([self.ph] * len(c.value))
            return f"{field_expr} {op_sql} ({placeholders})", list(c.value)

        return f"{field_expr} {op_sql} {self.ph}", [c.value]

    def select_objects_by_query(
        self,
        table: str,
        target_name: str,
        conditions: list[Condition | OrGroup],
    ) -> tuple[str, list[Any]]:
        """Build a SELECT query filtered by model name and conditions.

        Always includes a ``name = ?`` clause for type isolation.
        Additional conditions are ANDed together; ``OrGroup`` items are
        expanded into ``(a OR b ...)`` sub-clauses.

        Args:
            table: The objects table name.
            target_name: The ``__centauro_name__`` to filter by.
            conditions: User-provided filter conditions or OR groups.

        Returns:
            A ``(sql, params)`` tuple ready for execution.
        """
        where, params = self._build_where(target_name, conditions)
        return f"SELECT * FROM {table} WHERE {where}", params

    def select_objects_by_aggregate_field(
        self,
        table: str,
        target_name: str,
        field: str,
        aggregate: Literal["max", "min"],
        field_type: type,
        conditions: list[Condition | OrGroup],
    ) -> tuple[str, list[Any]]:
        where, params = self._build_where(target_name, conditions)
        field_expr = self._aggregate_field_expr(field, field_type)
        agg_sql = aggregate.upper()
        sql = (
            f"SELECT * FROM {table} WHERE {where} AND "
            f"{field_expr} = (SELECT {agg_sql}({field_expr}) FROM {table} WHERE {where})"
        )
        return sql, params + params

    def delete_object(self, table: str) -> str:
        """Build a DELETE query for a single object by id.

        Args:
            table: The objects table name.

        Returns:
            A SQL string with one placeholder for the row id.
        """
        return f"DELETE FROM {table} WHERE id = {self.ph}"

    def delete_all_objects(self, table: str) -> str:
        """Build a DELETE query for all objects of a given type.

        Args:
            table: The objects table name.

        Returns:
            A SQL string with one placeholder for the
            ``__centauro_name__``.
        """
        return f"DELETE FROM {table} WHERE name = {self.ph}"

    def delete_objects_by_query(
        self,
        table: str,
        target_name: str,
        conditions: list[Condition | OrGroup],
    ) -> tuple[str, list[Any]]:
        """Build a DELETE query filtered by model name and conditions.

        Args:
            table: The objects table name.
            target_name: The ``__centauro_name__`` to filter by.
            conditions: User-provided filter conditions or OR groups.

        Returns:
            A ``(sql, params)`` tuple ready for execution.
        """
        where, params = self._build_where(target_name, conditions)
        return f"DELETE FROM {table} WHERE {where}", params

    def select_all_objects(self, table: str) -> str:
        """Build a SELECT for all objects of a given type.

        Returns all rows whose ``name`` matches the given
        ``__centauro_name__``.

        Args:
            table: The objects table name.

        Returns:
            A SQL string with one placeholder for the
            ``__centauro_name__``.
        """
        return f"SELECT * FROM {table} WHERE name = {self.ph}"

    def select_latest_object(self, table: str) -> str:
        """Build a SELECT for the most recently edited object of a given type.

        Returns the single row with the highest ``edit_time``.  The
        ``id DESC`` tiebreaker ensures deterministic results when
        multiple rows share the same ``edit_time``.

        Args:
            table: The objects table name.

        Returns:
            A SQL string with one placeholder for the
            ``__centauro_name__``.
        """
        return (
            f"SELECT * FROM {table} WHERE name = {self.ph} "
            f"ORDER BY edit_time DESC, id DESC LIMIT 1"
        )


class SQLiteDialect(_QueryMixin):
    """SQL fragment generator for SQLite backends."""

    ph = "?"

    def _json_field_expr(self, field: str) -> str:
        """Return ``json_extract(meta, '$.field')`` for SQLite."""
        return f"json_extract(meta, '$.{field}')"

    def _aggregate_cast(self, expr: str, field_type: type) -> str:
        if field_type is int:
            return f"CAST({expr} AS INTEGER)"
        if field_type is float:
            return f"CAST({expr} AS REAL)"
        if field_type is date:
            return f"date({expr})"
        if field_type is datetime:
            return f"datetime({expr})"
        return expr

    def create_objects_table(self, table: str) -> str:
        """Return CREATE TABLE SQL for the objects table."""
        return (
            f"CREATE TABLE IF NOT EXISTS {table} ("
            f"id INTEGER PRIMARY KEY AUTOINCREMENT, "
            f"name TEXT NOT NULL, "
            f"write_time DATETIME NOT NULL, "
            f"edit_time DATETIME NOT NULL, "
            f"meta JSON NOT NULL"
            f")"
        )

    def create_objects_index(self, name: str, table: str) -> str:
        """Return CREATE INDEX SQL for the name column."""
        return f"CREATE INDEX IF NOT EXISTS idx_{name}_name ON {table} (name)"

    def create_values_table(self, table: str, objects_table: str) -> str:
        """Return CREATE TABLE SQL for the values table."""
        return (
            f"CREATE TABLE IF NOT EXISTS {table} ("
            f"data_object_id INTEGER NOT NULL "
            f"REFERENCES {objects_table}(id) ON DELETE CASCADE, "
            f"time DATETIME NOT NULL, "
            f"value REAL NOT NULL, "
            f"write_time DATETIME NOT NULL, "
            f"PRIMARY KEY (data_object_id, time)"
            f")"
        )

    def insert_object(self, table: str) -> str:
        """Return INSERT SQL for an object row."""
        return (
            f"INSERT INTO {table} (name, write_time, edit_time, meta) "
            f"VALUES ({self.ph}, {self.ph}, {self.ph}, json({self.ph}))"
        )

    def update_object(self, table: str) -> str:
        """Return UPDATE SQL for an object row."""
        return (
            f"UPDATE {table} SET edit_time = {self.ph}, "
            f"meta = json({self.ph}) WHERE id = {self.ph}"
        )

    def select_object(self, table: str) -> str:
        """Return SELECT SQL for a single object by id."""
        return f"SELECT * FROM {table} WHERE id = {self.ph}"

    def insert_values(self, table: str) -> str:
        """Return INSERT SQL for a values row."""
        return (
            f"INSERT INTO {table} (data_object_id, time, value, write_time) "
            f"VALUES ({self.ph}, {self.ph}, {self.ph}, {self.ph})"
        )

    def insert_or_ignore_values(self, table: str) -> str:
        """Return INSERT OR IGNORE SQL for a values row."""
        return (
            f"INSERT OR IGNORE INTO {table} "
            f"(data_object_id, time, value, write_time) "
            f"VALUES ({self.ph}, {self.ph}, {self.ph}, {self.ph})"
        )

    def upsert_values(self, table: str) -> str:
        """Return INSERT OR REPLACE SQL for a values row."""
        return (
            f"INSERT OR REPLACE INTO {table} "
            f"(data_object_id, time, value, write_time) "
            f"VALUES ({self.ph}, {self.ph}, {self.ph}, {self.ph})"
        )

    def delete_values(self, table: str) -> str:
        """Return DELETE SQL for all values of a given object."""
        return f"DELETE FROM {table} WHERE data_object_id = {self.ph}"

    def select_values(self, table: str) -> str:
        """Return SELECT SQL for values ordered by time."""
        return (
            f"SELECT time, value FROM {table} "
            f"WHERE data_object_id = {self.ph} ORDER BY time"
        )

    def json_col(self, field: str) -> str:
        """Return a json_extract column expression for a view."""
        return f"json_extract(meta, '$.{field}') AS {field}"

    def coalesce_json_cols(self, field: str, json_keys: list[str]) -> str:
        """Return a COALESCE expression over multiple JSON keys for renamed fields."""
        extracts = ", ".join(f"json_extract(meta, '$.{k}')" for k in json_keys)
        return f"COALESCE({extracts}) AS {field}"

    def create_expression_index(self, name: str, table: str, field: str) -> str:
        """Return CREATE INDEX SQL for a json_extract expression."""
        return (
            f"CREATE INDEX IF NOT EXISTS idx_{name}_{field} "
            f"ON {table} (json_extract(meta, '$.{field}'))"
        )

    def create_plain_index(self, name: str, table: str, field: str) -> str:
        """Return CREATE INDEX SQL for a plain (non-JSON) column."""
        return f"CREATE INDEX IF NOT EXISTS idx_{name}_{field} ON {table} ({field})"

    def drop_index(self, index_name: str) -> str:
        """Return DROP INDEX SQL. Silent if the index does not exist."""
        return f"DROP INDEX IF EXISTS {index_name}"

    def check_view_exists(self) -> str:
        """Return SQL to check if a view exists in sqlite_master."""
        return f"SELECT 1 FROM sqlite_master WHERE type='view' AND name={self.ph}"

    def list_views(self) -> str:
        """Return SQL to list views matching a LIKE pattern."""
        return (
            f"SELECT name FROM sqlite_master WHERE type='view' AND name LIKE {self.ph}"
        )

    def list_indexes(self) -> str:
        """Return SQL to list indexes on a table."""
        return (
            f"SELECT name FROM sqlite_master WHERE type='index' AND tbl_name={self.ph}"
        )

    def list_view_columns(self, view_name: str) -> str:
        """Return SQL to list column names for a view.

        SQLite does not support placeholders in ``PRAGMA`` statements,
        so the *view_name* is interpolated directly.  Callers must
        ensure the name has already been validated.
        """
        return f"PRAGMA table_info({view_name})"

    def drop_view(self, name: str) -> str:
        """Return DROP VIEW SQL."""
        return f"DROP VIEW {name}"

    def create_view(
        self, name: str, table: str, json_cols: str, model_names: str
    ) -> str:
        """Return CREATE VIEW SQL with extracted JSON columns."""
        return (
            f"CREATE VIEW {name} AS "
            f"SELECT id, name, write_time, edit_time, {json_cols} "
            f"FROM {table} "
            f"WHERE name IN ({model_names})"
        )

    def setup_statements(self, path: str) -> list[str]:
        """Return PRAGMA statements to run on connection init."""
        stmts = ["PRAGMA foreign_keys=ON"]
        if path != ":memory:":
            stmts.insert(0, "PRAGMA journal_mode=WAL")
        return stmts


class PostgresDialect(_QueryMixin):
    """SQL fragment generator for PostgreSQL backends."""

    ph = "%s"

    def _json_field_expr(self, field: str) -> str:
        """Return ``meta->>'field'`` for PostgreSQL."""
        return f"meta->>'{field}'"

    def _numeric_cast(self, expr: str) -> str:
        """Wrap expression in ``::numeric`` cast for PostgreSQL."""
        return f"({expr})::numeric"

    def _aggregate_cast(self, expr: str, field_type: type) -> str:
        if field_type is int:
            return f"CAST({expr} AS INTEGER)"
        if field_type is float:
            return f"CAST({expr} AS DOUBLE PRECISION)"
        if field_type is date:
            return f"CAST({expr} AS DATE)"
        if field_type is datetime:
            return f"CAST({expr} AS TIMESTAMPTZ)"
        return expr

    def create_objects_table(self, table: str) -> str:
        """Return CREATE TABLE SQL for the objects table."""
        return (
            f"CREATE TABLE IF NOT EXISTS {table} ("
            f"id SERIAL PRIMARY KEY, "
            f"name TEXT NOT NULL, "
            f"write_time TIMESTAMPTZ NOT NULL, "
            f"edit_time TIMESTAMPTZ NOT NULL, "
            f"meta JSONB NOT NULL"
            f")"
        )

    def create_objects_index(self, name: str, table: str) -> str:
        """Return CREATE INDEX SQL for the name column."""
        return f"CREATE INDEX IF NOT EXISTS idx_{name}_name ON {table} (name)"

    def create_values_table(self, table: str, objects_table: str) -> str:
        """Return CREATE TABLE SQL for the values table."""
        return (
            f"CREATE TABLE IF NOT EXISTS {table} ("
            f"data_object_id INTEGER NOT NULL "
            f"REFERENCES {objects_table}(id) ON DELETE CASCADE, "
            f"time TIMESTAMPTZ NOT NULL, "
            f"value DOUBLE PRECISION NOT NULL, "
            f"write_time TIMESTAMPTZ NOT NULL, "
            f"PRIMARY KEY (data_object_id, time)"
            f")"
        )

    def insert_object(self, table: str) -> str:
        """Return INSERT ... RETURNING id SQL for an object row."""
        return (
            f"INSERT INTO {table} (name, write_time, edit_time, meta) "
            f"VALUES ({self.ph}, {self.ph}, {self.ph}, {self.ph}::jsonb) "
            f"RETURNING id"
        )

    def update_object(self, table: str) -> str:
        """Return UPDATE SQL for an object row."""
        return (
            f"UPDATE {table} SET edit_time = {self.ph}, "
            f"meta = {self.ph}::jsonb WHERE id = {self.ph}"
        )

    def select_object(self, table: str) -> str:
        """Return SELECT SQL for a single object by id."""
        return f"SELECT * FROM {table} WHERE id = {self.ph}"

    def insert_values(self, table: str) -> str:
        """Return INSERT SQL for a values row."""
        return (
            f"INSERT INTO {table} (data_object_id, time, value, write_time) "
            f"VALUES ({self.ph}, {self.ph}, {self.ph}, {self.ph})"
        )

    def insert_or_ignore_values(self, table: str) -> str:
        """Return INSERT ... ON CONFLICT DO NOTHING SQL for a values row."""
        return (
            f"INSERT INTO {table} "
            f"(data_object_id, time, value, write_time) "
            f"VALUES ({self.ph}, {self.ph}, {self.ph}, {self.ph}) "
            f"ON CONFLICT DO NOTHING"
        )

    def upsert_values(self, table: str) -> str:
        """Return INSERT ... ON CONFLICT DO UPDATE SQL for a values row."""
        return (
            f"INSERT INTO {table} "
            f"(data_object_id, time, value, write_time) "
            f"VALUES ({self.ph}, {self.ph}, {self.ph}, {self.ph}) "
            f"ON CONFLICT (data_object_id, time) "
            f"DO UPDATE SET value=EXCLUDED.value, write_time=EXCLUDED.write_time"
        )

    def delete_values(self, table: str) -> str:
        """Return DELETE SQL for all values of a given object."""
        return f"DELETE FROM {table} WHERE data_object_id = {self.ph}"

    def select_values(self, table: str) -> str:
        """Return SELECT SQL for values ordered by time."""
        return (
            f"SELECT time, value FROM {table} "
            f"WHERE data_object_id = {self.ph} ORDER BY time"
        )

    def json_col(self, field: str) -> str:
        """Return a ``meta->>'field'`` column expression for a view."""
        return f"meta->>'{field}' AS {field}"

    def coalesce_json_cols(self, field: str, json_keys: list[str]) -> str:
        """Return a COALESCE expression over multiple JSON keys for renamed fields."""
        extracts = ", ".join(f"meta->>'{k}'" for k in json_keys)
        return f"COALESCE({extracts}) AS {field}"

    def create_expression_index(self, name: str, table: str, field: str) -> str:
        """Return CREATE INDEX SQL for a ``meta->>'field'`` expression."""
        return (
            f"CREATE INDEX IF NOT EXISTS idx_{name}_{field} "
            f"ON {table} ((meta->>'{field}'))"
        )

    def create_plain_index(self, name: str, table: str, field: str) -> str:
        """Return CREATE INDEX SQL for a plain (non-JSON) column."""
        return f"CREATE INDEX IF NOT EXISTS idx_{name}_{field} ON {table} ({field})"

    def drop_index(self, index_name: str) -> str:
        """Return DROP INDEX SQL. Silent if the index does not exist."""
        return f"DROP INDEX IF EXISTS {index_name}"

    def check_view_exists(self) -> str:
        """Return SQL to check if a view exists in information_schema."""
        return f"SELECT 1 FROM information_schema.views WHERE table_name = {self.ph}"

    def list_views(self) -> str:
        """Return SQL to list views matching a LIKE pattern."""
        return (
            f"SELECT table_name AS name FROM information_schema.views "
            f"WHERE table_name LIKE {self.ph}"
        )

    def list_indexes(self) -> str:
        """Return SQL to list indexes on a table."""
        return f"SELECT indexname AS name FROM pg_indexes WHERE tablename = {self.ph}"

    def list_view_columns(self, view_name: str) -> str:
        """Return SQL to list column names for a view.

        Uses ``information_schema.columns`` with a placeholder for the
        view name.  The *view_name* argument is unused here — it exists
        only for signature compatibility with ``SQLiteDialect`` where
        the name is baked into the PRAGMA string.  Pass the actual
        view name as a query parameter instead.
        """
        return (
            f"SELECT column_name AS name "
            f"FROM information_schema.columns "
            f"WHERE table_name = {self.ph} "
            f"ORDER BY ordinal_position"
        )

    def drop_view(self, name: str) -> str:
        """Return DROP VIEW SQL."""
        return f"DROP VIEW {name}"

    def create_view(
        self, name: str, table: str, json_cols: str, model_names: str
    ) -> str:
        """Return CREATE VIEW SQL with extracted JSON columns."""
        return (
            f"CREATE VIEW {name} AS "
            f"SELECT id, name, write_time, edit_time, {json_cols} "
            f"FROM {table} "
            f"WHERE name IN ({model_names})"
        )

    def setup_statements(self, path: str) -> list[str]:
        """Return statements to run on connection init (none for PostgreSQL)."""
        return []
