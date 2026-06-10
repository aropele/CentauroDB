from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, TypeVar, overload

if TYPE_CHECKING:
    import polars as pl

from centaurodb.collection import Collection, TimeSeriesCollection
from centaurodb.engine import Engine, RowProtocol
from centaurodb.exceptions import ThreadSafetyError
from centaurodb.model import CentauroModel
from centaurodb.query import Condition, Field, FieldProxy, OrGroup

T = TypeVar("T", bound=CentauroModel)


class AsyncCollection:
    """Async wrapper around ``Collection`` for use in async/await code.

    Every method delegates to the underlying sync ``Collection`` via
    ``asyncio.to_thread`` so the event loop is never blocked.

    The ``Engine`` **must** be created with ``check_same_thread=False``
    because ``to_thread`` dispatches work to a background thread.

    Args:
        conn: An Engine instance created with
            ``check_same_thread=False``.
        name: Collection name (same rules as ``Collection``).

    Raises:
        ThreadSafetyError: If the engine was created with the default
            ``check_same_thread=True``.

    Examples:
        >>> conn = Engine("my.db", check_same_thread=False)
        >>> col = AsyncCollection(conn, "library")
        >>> await col.write_object(Book(title="Dune", genre="sci-fi"))
        >>> book = await col.read_object_by_id(1, Book)
    """

    def __init__(self, conn: Engine, name: str):
        if conn.async_safety == "unsafe":
            raise ThreadSafetyError()
        self._col = Collection(conn, name)

    @property
    def conn(self) -> Engine:
        """The underlying Engine instance."""
        return self._col.conn

    @property
    def name(self) -> str:
        """The collection name."""
        return self._col.name

    async def write_object(
        self, obj: CentauroModel, *, duplicate: bool = False
    ) -> None:
        """Insert an object into the collection.

        See ``Collection.write_object`` for full documentation.

        Examples:
            >>> await col.write_object(Book(title="Dune", genre="sci-fi"))
        """
        await asyncio.to_thread(self._col.write_object, obj, duplicate=duplicate)

    async def write_objects(
        self, objs: Sequence[CentauroModel], *, duplicate: bool = False
    ) -> None:
        """Insert multiple objects in a single atomic transaction.

        See ``Collection.write_objects`` for full documentation.

        Examples:
            >>> await col.write_objects([Book(title="Dune"), Book(title="Foundation")])
        """
        await asyncio.to_thread(self._col.write_objects, objs, duplicate=duplicate)

    async def read_object_by_id(
        self, id: int, model_class: type[T], *, raw: bool = False
    ) -> T | RowProtocol:
        """Read a single object by id.

        See ``Collection.read_object_by_id`` for full documentation.

        Examples:
            >>> book = await col.read_object_by_id(1, Book)
        """
        return await asyncio.to_thread(
            self._col.read_object_by_id, id, model_class, raw=raw
        )

    async def read_objects(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        raw: bool = False,
        max_field: Field | None = None,
        min_field: Field | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[CentauroModel] | list[dict]:
        """Read objects, optionally filtered by conditions.

        See ``Collection.read_objects`` for full documentation.

        Examples:
            >>> results = await col.read_objects(Book)
            >>> results = await col.read_objects(
            ...     Book.fields.genre == "sci-fi",
            ... )
            >>> page = await col.read_objects(Book, limit=20, offset=40)
        """
        return await asyncio.to_thread(
            self._col.read_objects,
            *args,
            raw=raw,
            max_field=max_field,
            min_field=min_field,
            limit=limit,
            offset=offset,
        )

    async def count_objects(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
    ) -> int:
        """Return the number of objects matching the filter.

        See ``Collection.count_objects`` for full documentation.

        Examples:
            >>> n = await col.count_objects(Book.fields.genre == "sci-fi")
        """
        return await asyncio.to_thread(self._col.count_objects, *args)

    @overload
    async def aggregate(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        count: Literal[True],
        sum: None = None,
        avg: None = None,
        group_by: None = None,
    ) -> int: ...

    @overload
    async def aggregate(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        count: bool = False,
        sum: Field | None = None,
        avg: Field | None = None,
        group_by: Field | None = None,
    ) -> int | float | dict | pl.DataFrame: ...

    async def aggregate(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        count: bool = False,
        sum: Field | None = None,
        avg: Field | None = None,
        group_by: Field | None = None,
    ) -> int | float | dict | pl.DataFrame:
        """Multi-row aggregation entry point.

        See ``Collection.aggregate`` for full documentation. v1.0 only
        implements ``count=True``; sum/avg/group_by raise
        ``NotImplementedError``.

        Examples:
            >>> n = await col.aggregate(Book, count=True)
        """
        return await asyncio.to_thread(
            self._col.aggregate,
            *args,
            count=count,
            sum=sum,
            avg=avg,
            group_by=group_by,
        )

    async def read_latest_object(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        raw: bool = False,
        max_field: Field | None = None,
        min_field: Field | None = None,
    ) -> CentauroModel | dict | None:
        """Return the most recently edited object, optionally filtered.

        See ``Collection.read_latest_object`` for full documentation.

        Examples:
            >>> latest = await col.read_latest_object(Book)
            >>> latest = await col.read_latest_object(
            ...     Book.fields.genre == "sci-fi",
            ... )
        """
        return await asyncio.to_thread(
            self._col.read_latest_object,
            *args,
            raw=raw,
            max_field=max_field,
            min_field=min_field,
        )

    async def update_object(self, obj: CentauroModel) -> None:
        """Update an existing object.

        See ``Collection.update_object`` for full documentation.

        Examples:
            >>> book.genre = "fantasy"
            >>> await col.update_object(book)
        """
        await asyncio.to_thread(self._col.update_object, obj)

    async def delete_object(self, obj: CentauroModel) -> None:
        """Delete an object from the collection.

        See ``Collection.delete_object`` for full documentation.

        Examples:
            >>> await col.delete_object(book)
        """
        await asyncio.to_thread(self._col.delete_object, obj)

    async def delete_objects(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
    ) -> int:
        """Delete objects, optionally filtered by conditions.

        See ``Collection.delete_objects`` for full documentation.

        Examples:
            >>> count = await col.delete_objects(Book)
        """
        return await asyncio.to_thread(self._col.delete_objects, *args)

    async def refresh(self, obj: CentauroModel) -> None:
        """Re-read the object from the database.

        See ``Collection.refresh`` for full documentation.

        Examples:
            >>> await col.refresh(book)
        """
        await asyncio.to_thread(self._col.refresh, obj)

    async def write_child(
        self,
        child: CentauroModel,
        *,
        parent: CentauroModel,
        duplicate: bool = False,
    ) -> None:
        """Link a child to its parent and write it in one step.

        See ``Collection.write_child`` for full documentation.

        Examples:
            >>> await col.write_child(chapter, parent=book)
        """
        await asyncio.to_thread(
            self._col.write_child, child, parent=parent, duplicate=duplicate
        )

    async def children_of(
        self,
        parent: CentauroModel,
        child_class: type[T],
        *,
        raw: bool = False,
    ) -> list[T] | list[dict]:
        """Read all children linked to a parent object.

        See ``Collection.children_of`` for full documentation.

        Examples:
            >>> chapters = await col.children_of(book, Chapter)
        """
        return await asyncio.to_thread(
            self._col.children_of, parent, child_class, raw=raw
        )

    async def create_view(
        self,
        name: str,
        *models: type[CentauroModel],
        include_fields: list[str] | None = None,
        overwrite: bool = False,
    ) -> None:
        """Create a SQL VIEW.

        See ``Collection.create_view`` for full documentation.

        Examples:
            >>> await col.create_view("all_books", Book)
        """
        await asyncio.to_thread(
            self._col.create_view,
            name,
            *models,
            include_fields=include_fields,
            overwrite=overwrite,
        )

    async def refresh_view(
        self,
        name: str,
        *models: type[CentauroModel],
        include_fields: list[str] | None = None,
    ) -> None:
        """Drop and recreate a view after a schema change.

        Only needed when model fields are renamed, added, or removed.
        Row changes do not require a refresh. Indexes are managed
        separately via ``create_index()`` / ``drop_index()``.

        See ``Collection.refresh_view`` for full documentation.

        Examples:
            >>> await col.refresh_view("all_books", BookV2)
        """
        await asyncio.to_thread(
            self._col.refresh_view,
            name,
            *models,
            include_fields=include_fields,
        )

    async def create_index(self, field: str) -> None:
        """Create an index on a field.

        See ``Collection.create_index`` for full documentation.

        Examples:
            >>> await col.create_index("status")
        """
        await asyncio.to_thread(self._col.create_index, field)

    async def drop_index(self, field: str) -> None:
        """Drop an index by field name.

        See ``Collection.drop_index`` for full documentation.

        Examples:
            >>> await col.drop_index("status")
        """
        await asyncio.to_thread(self._col.drop_index, field)

    async def list_indexes(self, *, raw: bool = False) -> list[str]:
        """List user-created indexes on this collection.

        See ``Collection.list_indexes`` for full documentation.

        Examples:
            >>> await col.list_indexes()
            ['status', 'write_time']
        """
        return await asyncio.to_thread(self._col.list_indexes, raw=raw)

    async def sql_select(self, sql: str, params: tuple = ()):
        """Execute a read-only SELECT query and return a Polars DataFrame.

        See ``Collection.sql_select`` for full documentation.

        Examples:
            >>> df = await col.sql_select("SELECT * FROM sensors_view_dash WHERE temp > ?", (20,))
        """
        return await asyncio.to_thread(self._col.sql_select, sql, params)

    async def describe_views(self) -> dict[str, list[str]]:
        """List all views and their column names.

        See ``Collection.describe_views`` for full documentation.

        Examples:
            >>> views = await col.describe_views()
        """
        return await asyncio.to_thread(self._col.describe_views)


class AsyncTimeSeriesCollection:
    """Async wrapper around ``TimeSeriesCollection`` for async/await code.

    Every method delegates to the underlying sync
    ``TimeSeriesCollection`` via ``asyncio.to_thread``.

    Args:
        conn: An Engine instance created with
            ``check_same_thread=False``.
        name: Collection name (same rules as ``TimeSeriesCollection``).

    Raises:
        ThreadSafetyError: If the engine was created with the default
            ``check_same_thread=True``.

    Examples:
        >>> conn = Engine("my.db", check_same_thread=False)
        >>> col = AsyncTimeSeriesCollection(conn, "metrics")
        >>> await col.write_object(metric)
        >>> loaded = await col.read_object_by_id(1, Metric)
    """

    def __init__(self, conn: Engine, name: str):
        if conn.async_safety == "unsafe":
            raise ThreadSafetyError()
        self._col = TimeSeriesCollection(conn, name)

    @property
    def conn(self) -> Engine:
        """The underlying Engine instance."""
        return self._col.conn

    @property
    def name(self) -> str:
        """The collection name."""
        return self._col.name

    async def write_object(
        self, obj: CentauroModel, *, duplicate: bool = False
    ) -> None:
        """Insert an object and its values.

        See ``TimeSeriesCollection.write_object`` for full documentation.

        Examples:
            >>> await col.write_object(metric)
        """
        await asyncio.to_thread(self._col.write_object, obj, duplicate=duplicate)

    async def write_objects(
        self, objs: Sequence[CentauroModel], *, duplicate: bool = False
    ) -> None:
        """Insert multiple time-series objects in a single atomic transaction.

        See ``TimeSeriesCollection.write_objects`` for full documentation.

        Examples:
            >>> await col.write_objects([Metric(unit="kg"), Metric(unit="m")])
        """
        await asyncio.to_thread(self._col.write_objects, objs, duplicate=duplicate)

    async def read_object_by_id(
        self,
        id: int,
        model_class: type[T],
        *,
        raw: bool = False,
        hydrate_values: bool = True,
    ) -> T | RowProtocol:
        """Read an object and optionally its values.

        See ``TimeSeriesCollection.read_object_by_id`` for full documentation.

        Examples:
            >>> metric = await col.read_object_by_id(1, Metric)
            >>> len(metric.values)
            5
        """
        return await asyncio.to_thread(
            self._col.read_object_by_id,
            id,
            model_class,
            raw=raw,
            hydrate_values=hydrate_values,
        )

    async def read_objects(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        raw: bool = False,
        hydrate_values: bool = True,
        max_field: Field | None = None,
        min_field: Field | None = None,
    ) -> list[CentauroModel] | list[dict]:
        """Read objects, optionally filtered, and hydrate time-series values.

        See ``TimeSeriesCollection.read_objects`` for full documentation.

        Examples:
            >>> results = await col.read_objects(Metric)
            >>> results = await col.read_objects(
            ...     Metric.fields.unit == "kg",
            ... )
        """
        return await asyncio.to_thread(
            self._col.read_objects,
            *args,
            raw=raw,
            hydrate_values=hydrate_values,
            max_field=max_field,
            min_field=min_field,
        )

    async def count_objects(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
    ) -> int:
        """Return the number of objects matching the filter.

        See ``TimeSeriesCollection.count_objects`` for full documentation.

        Examples:
            >>> n = await col.count_objects(Metric.fields.unit == "kg")
        """
        return await asyncio.to_thread(self._col.count_objects, *args)

    @overload
    async def aggregate(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        count: Literal[True],
        sum: None = None,
        avg: None = None,
        group_by: None = None,
    ) -> int: ...

    @overload
    async def aggregate(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        count: bool = False,
        sum: Field | None = None,
        avg: Field | None = None,
        group_by: Field | None = None,
    ) -> int | float | dict | pl.DataFrame: ...

    async def aggregate(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        count: bool = False,
        sum: Field | None = None,
        avg: Field | None = None,
        group_by: Field | None = None,
    ) -> int | float | dict | pl.DataFrame:
        """Multi-row aggregation entry point.

        See ``TimeSeriesCollection.aggregate`` for full documentation.
        v1.0 only implements ``count=True``.

        Examples:
            >>> n = await col.aggregate(Metric, count=True)
        """
        return await asyncio.to_thread(
            self._col.aggregate,
            *args,
            count=count,
            sum=sum,
            avg=avg,
            group_by=group_by,
        )

    async def read_latest_object(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        raw: bool = False,
        hydrate_values: bool = True,
        max_field: Field | None = None,
        min_field: Field | None = None,
    ) -> CentauroModel | dict | None:
        """Return the most recently edited object, optionally filtered and with values.

        See ``TimeSeriesCollection.read_latest_object`` for full
        documentation.

        Examples:
            >>> latest = await col.read_latest_object(Metric)
            >>> latest = await col.read_latest_object(
            ...     Metric.fields.unit == "kg",
            ... )
        """
        return await asyncio.to_thread(
            self._col.read_latest_object,
            *args,
            raw=raw,
            hydrate_values=hydrate_values,
            max_field=max_field,
            min_field=min_field,
        )

    async def write_child(
        self,
        child: CentauroModel,
        *,
        parent: CentauroModel,
        duplicate: bool = False,
    ) -> None:
        """Link a child to its parent and write it in one step.

        See ``TimeSeriesCollection.write_child`` for full documentation.

        Examples:
            >>> await col.write_child(chapter, parent=book)
        """
        await asyncio.to_thread(
            self._col.write_child, child, parent=parent, duplicate=duplicate
        )

    async def children_of(
        self,
        parent: CentauroModel,
        child_class: type[T],
        *,
        raw: bool = False,
    ) -> list[T] | list[dict]:
        """Read all children linked to a parent object.

        See ``TimeSeriesCollection.children_of`` for full documentation.

        Examples:
            >>> chapters = await col.children_of(book, Chapter)
        """
        return await asyncio.to_thread(
            self._col.children_of, parent, child_class, raw=raw
        )

    async def update_object(
        self,
        obj: CentauroModel,
        *,
        values_mode: Literal["replace", "append", "upsert"] = "append",
    ) -> None:
        """Update an existing object and its values.

        See ``TimeSeriesCollection.update_object`` for full documentation.

        Examples:
            >>> await col.update_object(metric, values_mode="append")
        """
        await asyncio.to_thread(self._col.update_object, obj, values_mode=values_mode)

    async def delete_object(self, obj: CentauroModel) -> None:
        """Delete an object from the collection.

        See ``TimeSeriesCollection.delete_object`` for full documentation.

        Examples:
            >>> await col.delete_object(metric)
        """
        await asyncio.to_thread(self._col.delete_object, obj)

    async def delete_objects(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
    ) -> int:
        """Delete objects, optionally filtered by conditions.

        See ``TimeSeriesCollection.delete_objects`` for full documentation.

        Examples:
            >>> count = await col.delete_objects(Metric)
        """
        return await asyncio.to_thread(self._col.delete_objects, *args)

    async def refresh(self, obj: CentauroModel) -> None:
        """Re-read the object and its values from the database.

        See ``TimeSeriesCollection.refresh`` for full documentation.

        Examples:
            >>> await col.refresh(metric)
        """
        await asyncio.to_thread(self._col.refresh, obj)

    async def create_view(
        self,
        name: str,
        *models: type[CentauroModel],
        include_fields: list[str] | None = None,
        overwrite: bool = False,
    ) -> None:
        """Create a SQL VIEW.

        See ``TimeSeriesCollection.create_view`` for full documentation.

        Examples:
            >>> await col.create_view("all", Metric)
        """
        await asyncio.to_thread(
            self._col.create_view,
            name,
            *models,
            include_fields=include_fields,
            overwrite=overwrite,
        )

    async def refresh_view(
        self,
        name: str,
        *models: type[CentauroModel],
        include_fields: list[str] | None = None,
    ) -> None:
        """Drop and recreate a view after a schema change.

        Only needed when model fields are renamed, added, or removed.
        Row changes do not require a refresh. Indexes are managed
        separately via ``create_index()`` / ``drop_index()``.

        See ``TimeSeriesCollection.refresh_view`` for full documentation.

        Examples:
            >>> await col.refresh_view("all", MetricV2)
        """
        await asyncio.to_thread(
            self._col.refresh_view,
            name,
            *models,
            include_fields=include_fields,
        )

    async def create_index(self, field: str) -> None:
        """Create an index on a field.

        See ``TimeSeriesCollection.create_index`` for full documentation.

        Examples:
            >>> await col.create_index("unit")
        """
        await asyncio.to_thread(self._col.create_index, field)

    async def drop_index(self, field: str) -> None:
        """Drop an index by field name.

        See ``TimeSeriesCollection.drop_index`` for full documentation.

        Examples:
            >>> await col.drop_index("unit")
        """
        await asyncio.to_thread(self._col.drop_index, field)

    async def list_indexes(self, *, raw: bool = False) -> list[str]:
        """List user-created indexes on this collection.

        See ``TimeSeriesCollection.list_indexes`` for full documentation.

        Examples:
            >>> await col.list_indexes()
            ['unit']
        """
        return await asyncio.to_thread(self._col.list_indexes, raw=raw)

    async def sql_select(self, sql: str, params: tuple = ()):
        """Execute a read-only SELECT query and return a Polars DataFrame.

        See ``TimeSeriesCollection.sql_select`` for full documentation.

        Examples:
            >>> df = await col.sql_select("SELECT * FROM metrics_view_dash WHERE unit = ?", ("kg",))
        """
        return await asyncio.to_thread(self._col.sql_select, sql, params)

    async def describe_views(self) -> dict[str, list[str]]:
        """List all views and their column names.

        See ``TimeSeriesCollection.describe_views`` for full documentation.

        Examples:
            >>> views = await col.describe_views()
        """
        return await asyncio.to_thread(self._col.describe_views)
