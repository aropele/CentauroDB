from __future__ import annotations

import re
import types
from collections.abc import Sequence
from datetime import date, datetime, timezone
from typing import (
    TYPE_CHECKING,
    Annotated,
    Literal,
    TypeVar,
    Union,
    get_args,
    get_origin,
    overload,
)

if TYPE_CHECKING:
    import polars as pl

from pydantic import AliasChoices

from centaurodb.engine import Engine, RowProtocol
from centaurodb.exceptions import (
    ObjectAlreadyPersistedError,
    ObjectNotFoundError,
    ObjectNotPersistedError,
    ParentHasChildrenError,
    UnlinkedParentError,
    ViewAlreadyExistsError,
)
from centaurodb.model import (
    CentauroModel,
    CentauroModelSeries,
    CentauroValues,
    RowFields,
)
from centaurodb.query import Condition, Field, FieldProxy, OrGroup

T = TypeVar("T", bound=CentauroModel)


def _validate_meta(model_class: type[T], meta: object) -> T:
    # psycopg auto-decodes jsonb columns to dict; sqlite returns TEXT as str.
    if isinstance(meta, dict):
        return model_class.model_validate(meta)
    return model_class.model_validate_json(meta)  # type: ignore[arg-type]


_PLAIN_COLUMNS = {"id", "name", "write_time", "edit_time"}
_MANAGED_FIELDS = {
    "name"
}  # indexes auto-created by CentauroDB — must not be user-dropped

_AGGREGATE_TYPE_MAP: dict[str, type] = {
    "id": int,
    "write_time": datetime,
    "edit_time": datetime,
}
_AGGREGATE_ALLOWED_TYPES = (int, float, date, datetime)


def _persistent_name(cls_or_obj: type[CentauroModel] | CentauroModel) -> str:
    """Return the ``__centauro_name__`` used for DB storage."""
    cls = cls_or_obj if isinstance(cls_or_obj, type) else type(cls_or_obj)
    return cls.__centauro_name__


def _unwrap_aggregate_annotation(annotation: object) -> type | None:
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        if not args:
            return None
        annotation = args[0]
        origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) != 1:
            return None
        annotation = non_none[0]
    return annotation if isinstance(annotation, type) else None


def _normalize_aggregate_type(annotation: object) -> type | None:
    base = _unwrap_aggregate_annotation(annotation)
    if base is None:
        return None
    if base is bool:
        return None
    if issubclass(base, datetime):
        return datetime
    if issubclass(base, date):
        return date
    if issubclass(base, int):
        return int
    if issubclass(base, float):
        return float
    return None


class BaseCollection:
    """Base class for named collections within a database.

    Creates a ``{name}_objects`` table and provides read/write methods
    for Pydantic objects stored as JSON blobs.

    How many collections?
    ---------------------
    A single collection maps to a single ``{name}_objects`` table.
    Every model type written into it shares that table, discriminated
    by the ``__centauro_name__`` column. **One collection per project
    is the recommended starting point.**

    You **must** use separate collections when mixing plain models
    (``CentauroModel``) with time-series models
    (``CentauroModelSeries``), because ``Collection`` and
    ``TimeSeriesCollection`` enforce their respective types.

    Beyond that, consider an additional collection only when:

    - **Unrelated domains** share the same database (e.g. trading
      signals vs. user preferences). Separate tables avoid
      irrelevant rows in full-type scans.
    - **Write-volume skew** — one model type produces orders of
      magnitude more rows than the others (~100x+). Isolating the
      hot writer reduces scan cost, WAL contention, and
      vacuum/maintenance overhead for the rest.
    - **Independent retention** — you need to purge one class of
      data without touching others. A dedicated table can be
      dropped or truncated cheaply.

    Do **not** split:

    - For organizational tidiness alone — ``__centauro_name__`` and
      views already separate model types logically.
    - Parent–child relationships (``parent_ref``, ``write_child``,
      ``children_of``) — these only work within a single collection.
      Splitting related models across collections breaks them.

    Naming guidelines
    -----------------
    The collection name becomes a **table prefix** in the database:
    ``{name}_objects``, ``{name}_values``, ``{name}_view_*``.  Choose
    a name that describes the **domain or project**, not a single
    model type — the collection will likely hold many types.

    Good names::

        Collection(conn, "inventory")     # inventory_objects
        Collection(conn, "research")     # research_objects
        Collection(conn, "app")          # app_objects

    Bad names::

        Collection(conn, "notes")        # too narrow — what happens
                                         # when you add Tags, Users?
        Collection(conn, "data")         # too vague
        Collection(conn, "my_objects")   # redundant — table is
                                         # already my_objects_objects

    Args:
        conn: An Engine instance.
        name: Collection name. Must be lowercase alphanumeric with
            underscores, starting with a letter.

    Raises:
        ValueError: If name contains invalid characters.
    """

    def __init__(self, conn: Engine, name: str):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(
                f"Collection name must be lowercase alphanumeric with underscores, got: '{name}'"
            )

        self.conn = conn
        self.name = name
        self._objects_table = f"{name}_objects"

        self._create_tables()

    def _create_tables(self):
        """Create the objects table and name index if they don't exist."""
        d = self.conn.dialect
        self.conn.execute(d.create_objects_table(self._objects_table))
        self.conn.execute(d.create_objects_index(self.name, self._objects_table))
        self.conn.commit()

    def _write_object_no_commit(
        self, obj: CentauroModel, *, duplicate: bool = False
    ) -> None:
        """Insert an object without committing the transaction.

        Internal helper used by ``write_objects`` to batch multiple
        inserts in a single transaction. Validates and inserts the
        object row but does **not** call ``commit()``.

        Args:
            obj: A CentauroModel instance to persist.
            duplicate: If True, force insert a new row even if the
                object has already been written.
        """
        if not isinstance(obj, CentauroModel):
            raise TypeError(
                f"Expected a CentauroModel instance, got {type(obj).__name__}"
            )

        if obj._persisted and not duplicate:
            if obj._row is None:
                raise ObjectAlreadyPersistedError(
                    _persistent_name(obj), -1, self.name, self._objects_table
                )
            raise ObjectAlreadyPersistedError(
                _persistent_name(obj),
                obj._row.id,
                self.name,
                self._objects_table,
            )

        refs = getattr(type(obj), "__centauro_refs__", {})
        for field_name, parent_cls in refs.items():
            if getattr(obj, field_name) is None:
                raise UnlinkedParentError(
                    type(obj).__name__, field_name, parent_cls.__name__
                )

        name = _persistent_name(obj)
        now = datetime.now(timezone.utc).isoformat()
        sql = self.conn.dialect.insert_object(self._objects_table)
        obj_id = self.conn.insert_returning_id(
            sql, (name, now, now, obj.model_dump_json())
        )
        obj._row = RowFields(
            id=obj_id,
            name=name,
            write_time=datetime.fromisoformat(now),
            edit_time=datetime.fromisoformat(now),
        )
        obj._persisted = True

    def write_object(self, obj: CentauroModel, *, duplicate: bool = False) -> None:
        """Insert an object into the collection.

        Serializes the model to JSON and stores it in the objects table.
        Sets ``obj._row`` and ``obj._persisted`` on success.

        Args:
            obj: A CentauroModel instance to persist.
            duplicate: If True, force insert a new row even if the object
                has already been written.

        Raises:
            TypeError: If obj is not a CentauroModel instance.
            ObjectAlreadyPersistedError: If the object has already been
                written and duplicate is False.

        Examples:
            >>> col = Collection(Engine(), "library")
            >>> book = Book(title="Dune", genre="sci-fi")
            >>> col.write_object(book)
            >>> book._persisted
            True
        """
        self._write_object_no_commit(obj, duplicate=duplicate)

    def write_objects(
        self, objs: Sequence[CentauroModel], *, duplicate: bool = False
    ) -> None:
        """Insert multiple objects in a single atomic transaction.

        All objects are validated and inserted within one transaction.
        If any object fails validation (e.g. wrong type, already
        persisted), **none** of the objects are persisted and the
        transaction is rolled back.

        Args:
            objs: A sequence of CentauroModel instances to persist.
            duplicate: If True, force insert new rows even for objects
                that have already been written.

        Raises:
            TypeError: If any element is not a CentauroModel instance.
            ObjectAlreadyPersistedError: If any object has already been
                written and duplicate is False.

        Examples:
            >>> books = [Book(title="Dune"), Book(title="Foundation")]
            >>> col.write_objects(books)
            >>> all(b._persisted for b in books)
            True
        """
        if not objs:
            return
        # Snapshot state so we can restore on rollback — objects that were
        # partially inserted before a failure would otherwise keep stale
        # _persisted=True / _row values pointing at rolled-back rows.
        saved = [
            (obj._persisted, obj._row) for obj in objs if isinstance(obj, CentauroModel)
        ]
        try:
            for obj in objs:
                self._write_object_no_commit(obj, duplicate=duplicate)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            cm_objs = [obj for obj in objs if isinstance(obj, CentauroModel)]
            for obj, (was_persisted, old_row) in zip(cm_objs, saved):
                obj._persisted = was_persisted
                obj._row = old_row
            raise

    def _update_object_no_commit(self, obj: CentauroModel) -> None:
        """Update an object's meta row without committing the transaction.

        Internal helper used by ``update_object`` and overridden in
        ``TimeSeriesCollection`` to keep meta + values updates atomic.

        Args:
            obj: A persisted CentauroModel instance.
        """
        if not isinstance(obj, CentauroModel):
            raise TypeError(
                f"Expected a CentauroModel instance, got {type(obj).__name__}"
            )
        if not obj._persisted:
            raise ObjectNotPersistedError(
                _persistent_name(obj), self.name, self._objects_table
            )
        if obj._row is None:
            raise ObjectNotPersistedError(
                _persistent_name(obj), self.name, self._objects_table
            )

        refs = getattr(type(obj), "__centauro_refs__", {})
        for field_name, parent_cls in refs.items():
            if getattr(obj, field_name) is None:
                raise UnlinkedParentError(
                    type(obj).__name__, field_name, parent_cls.__name__
                )

        now = datetime.now(timezone.utc).isoformat()
        sql = self.conn.dialect.update_object(self._objects_table)
        self.conn.execute(sql, (now, obj.model_dump_json(), obj._row.id))
        obj._row.edit_time = datetime.fromisoformat(now)

    def update_object(self, obj: CentauroModel) -> None:
        """Update an existing object in the collection.

        Writes the current state of the object to the database and
        updates the ``edit_time``. The ``write_time`` is preserved.

        Args:
            obj: A CentauroModel instance that has been previously
                persisted.

        Raises:
            TypeError: If obj is not a CentauroModel instance.
            ObjectNotPersistedError: If the object has never been
                written to the database.

        Examples:
            >>> book.genre = "fantasy"
            >>> col.update_object(book)
            >>> reloaded = col.read_object_by_id(book.row.id, Book)
            >>> reloaded.genre
            'fantasy'
        """
        self._update_object_no_commit(obj)
        self.conn.commit()

    def _hydrate_row(self, row: RowProtocol, model_class: type[T]) -> T:
        """Deserialize a database row into a model instance with row metadata."""
        expected_name = _persistent_name(model_class)
        if row["name"] != expected_name:
            raise ObjectNotFoundError(
                expected_name, row["id"], self.name, self._objects_table
            )
        obj = _validate_meta(model_class, row["meta"])
        obj._row = RowFields(
            id=row["id"],
            name=row["name"],
            write_time=row["write_time"],
            edit_time=row["edit_time"],
        )
        obj._persisted = True
        return obj

    def read_object_by_id(
        self, id: int, model_class: type[T], *, raw: bool = False
    ) -> T | RowProtocol:
        """Read a single object from the collection by its row id.

        Args:
            id: The row id of the object to read.
            model_class: The CentauroModel subclass to deserialize into.
            raw: If True, return the raw database row instead of a
                deserialized model instance.

        Returns:
            The deserialized model instance, or a dict-like row if
            raw is True.

        Raises:
            ObjectNotFoundError: If no object with the given id exists.

        Examples:
            >>> book = col.read_object_by_id(1, Book)
            >>> book.genre
            'sci-fi'
            >>> row = col.read_object_by_id(1, Book, raw=True)
            >>> row["meta"]
            '{"title": "Dune", "genre": "sci-fi"}'
        """
        sql = self.conn.dialect.select_object(self._objects_table)
        row = self.conn.fetchone(sql, (id,))
        if row is None:
            raise ObjectNotFoundError(
                _persistent_name(model_class),
                id,
                self.name,
                self._objects_table,
            )
        if raw:
            return row
        return self._hydrate_row(row, model_class)

    # ------------------------------------------------------------------
    # Shared helpers for read_latest_object / read_objects
    # ------------------------------------------------------------------

    def _resolve_read_args(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
    ) -> tuple[type[CentauroModel], str, list[Condition | OrGroup]]:
        """Parse positional read arguments into model class, name, and conditions.

        Supports three calling styles:

        1. **Model class** (with optional trailing conditions)::

               _resolve_read_args(Book)
               _resolve_read_args(Book, Book.fields.genre == "sci-fi")

        2. **Conditions** — model class inferred::

               _resolve_read_args(Book.fields.genre == "sci-fi")

        3. **FieldProxy** (legacy) — equivalent to the model class::

               _resolve_read_args(Book.fields)

        Returns:
            ``(model_class, target_name, conditions)`` where *conditions*
            is a (possibly empty) list of validated ``Condition`` /
            ``OrGroup``.
        """
        if not args:
            raise TypeError(
                "At least one argument is required. "
                "Pass a model class or conditions "
                "like Model.fields.field_name == value."
            )

        first = args[0]

        # Style 1: Model class (+ optional conditions)
        if isinstance(first, type) and issubclass(first, CentauroModel):
            model_class = first
            rest = args[1:]
            if rest and not all(isinstance(c, (Condition, OrGroup)) for c in rest):
                raise TypeError(
                    "When passing a model class, additional arguments "
                    "must be conditions (Model.fields.field == value)."
                )
            conditions: list[Condition | OrGroup] = list(rest)  # type: ignore[arg-type]

        # Style 3 (legacy): FieldProxy
        elif isinstance(first, FieldProxy):
            if len(args) > 1:
                raise TypeError(
                    "When passing Model.fields, no conditions should follow. "
                    "Use Model.fields.field_name == value to add conditions."
                )
            model_class = first._model
            conditions = []

        # Style 2: Conditions
        else:
            conditions = list(args)  # type: ignore[arg-type]
            model_classes: set[type] = set()
            for item in conditions:
                if isinstance(item, OrGroup):
                    model_classes.update(
                        c.model_class
                        for c in item.conditions
                        if c.model_class is not None
                    )
                elif isinstance(item, Condition) and item.model_class is not None:
                    model_classes.add(item.model_class)
            if not model_classes:
                raise ValueError(
                    "Cannot infer model class from conditions. "
                    "Use Model.fields.field_name syntax instead of Field('name')."
                )
            if len(model_classes) > 1:
                raise ValueError(
                    "All conditions must reference the same model class. Got: "
                    + ", ".join(m.__name__ for m in model_classes)
                )
            model_class = model_classes.pop()

        target_name = _persistent_name(model_class)

        # Validate condition field names
        valid_fields = set(model_class.model_fields) | {
            "id",
            "write_time",
            "edit_time",
        }
        for item in conditions:
            items_to_check = item.conditions if isinstance(item, OrGroup) else [item]
            for c in items_to_check:
                if c.field not in valid_fields:
                    raise ValueError(
                        f"Unknown field '{c.field}' for model '{model_class.__name__}'. "
                        f"Valid fields: {sorted(valid_fields)}"
                    )

        return model_class, target_name, conditions

    def _validate_aggregate_field(
        self,
        model_class: type[CentauroModel],
        aggregate_field: Field,
    ) -> tuple[str, type]:
        """Validate a ``max_field`` / ``min_field`` against the model.

        Returns:
            ``(field_name, field_type)`` ready for SQL generation.
        """
        valid_fields = set(model_class.model_fields) | {
            "id",
            "write_time",
            "edit_time",
        }
        if aggregate_field.model_class is None:
            raise ValueError(
                "max_field/min_field must be bound to a model class. "
                "Use Model.fields.field_name."
            )
        if aggregate_field.model_class is not model_class:
            raise ValueError(
                "max_field/min_field must belong to the same model class "
                "as the query conditions."
            )
        aggregate_name = aggregate_field.name
        if aggregate_name not in valid_fields:
            raise ValueError(
                f"Unknown field '{aggregate_name}' for model '{model_class.__name__}'. "
                f"Valid fields: {sorted(valid_fields)}"
            )
        field_type = _AGGREGATE_TYPE_MAP.get(aggregate_name)
        if field_type is None:
            field_info = model_class.model_fields[aggregate_name]
            field_type = _normalize_aggregate_type(field_info.annotation)
        if field_type is None or field_type not in _AGGREGATE_ALLOWED_TYPES:
            raise TypeError(
                "max_field/min_field only supports int, float, date, or "
                "datetime fields."
            )
        return aggregate_name, field_type

    def _fetch_read_rows(
        self,
        target_name: str,
        model_class: type[CentauroModel],
        conditions: list[Condition | OrGroup],
        *,
        max_field: Field | None = None,
        min_field: Field | None = None,
        latest: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list:
        """Build a read query, execute it, and return raw rows.

        When *latest* is ``True`` the result set is ordered by
        ``edit_time DESC, id DESC`` and limited to one row.

        When *limit* is set, results are ordered by ``id ASC`` and
        capped to *limit* rows; *offset* skips that many rows. The
        ordering keeps pagination deterministic across calls.
        """
        aggregate_field = max_field or min_field
        d = self.conn.dialect

        page_suffix = ""
        page_params: list[int] = []
        if limit is not None:
            if limit == 0:
                return []
            page_suffix = f" ORDER BY id ASC LIMIT {d.ph}"
            page_params = [limit]
            if offset:
                page_suffix += f" OFFSET {d.ph}"
                page_params.append(offset)

        if aggregate_field is not None:
            agg_name, field_type = self._validate_aggregate_field(
                model_class, aggregate_field
            )
            agg = "max" if max_field is not None else "min"
            sql, params = d.select_objects_by_aggregate_field(
                self._objects_table,
                target_name,
                agg_name,
                agg,
                field_type,
                conditions,
            )
            if latest:
                sql += " ORDER BY edit_time DESC, id DESC LIMIT 1"
            elif page_suffix:
                sql += page_suffix
                params = params + page_params
            return self.conn.fetchall(sql, tuple(params))

        if not conditions:
            if latest:
                sql_s = d.select_latest_object(self._objects_table)
                row = self.conn.fetchone(sql_s, (target_name,))
                return [row] if row is not None else []
            else:
                sql_s = d.select_all_objects(self._objects_table)
                if page_suffix:
                    sql_s += page_suffix
                    return self.conn.fetchall(sql_s, (target_name, *page_params))
                return self.conn.fetchall(sql_s, (target_name,))

        sql, params = d.select_objects_by_query(
            self._objects_table, target_name, conditions
        )
        if latest:
            sql += " ORDER BY edit_time DESC, id DESC LIMIT 1"
        elif page_suffix:
            sql += page_suffix
            params = params + page_params
        return self.conn.fetchall(sql, tuple(params))

    # ------------------------------------------------------------------
    # Public read methods
    # ------------------------------------------------------------------

    def read_latest_object(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        raw: bool = False,
        max_field: Field | None = None,
        min_field: Field | None = None,
    ) -> CentauroModel | dict | None:
        """Return the most recently edited object, optionally filtered.

        Supports the same calling styles as ``read_objects``:

        1. **Model class** — latest object of that type::

               col.read_latest_object(Book)

        2. **Conditions** — latest matching the given filters::

               col.read_latest_object(Book.fields.genre == "sci-fi")

        3. **Model class + conditions**::

               col.read_latest_object(
                   Book, Book.fields.genre == "sci-fi",
               )

        When ``max_field`` or ``min_field`` is given, the result is
        the most recently edited row among those matching the
        aggregate.

        .. warning::

           When two rows share the same ``edit_time`` the tiebreaker is
           ``id DESC``, which assumes sequential auto-increment IDs.

        Args:
            *args: A model class, ``FieldProxy``, or conditions.
            raw: If True, return the raw database row.
            max_field: Narrow to rows with the maximum value of this
                field before picking the latest.
            min_field: Narrow to rows with the minimum value of this
                field before picking the latest.

        Returns:
            The most recently edited model instance, a dict-like row
            if ``raw`` is True, or ``None`` if no matching objects
            exist.

        Examples:
            >>> col.write_object(Book(title="Dune", genre="sci-fi"))
            >>> col.write_object(Book(title="Foundation", genre="sci-fi"))
            >>> latest = col.read_latest_object(Book)
            >>> latest.title
            'Foundation'
            >>> col.read_latest_object(Author) is None
            True
            >>> col.read_latest_object(Book.fields.genre == "sci-fi")
            Book(title='Foundation', ...)
        """
        if max_field is not None and min_field is not None:
            raise TypeError("Use only one of max_field or min_field.")

        model_class, target_name, conditions = self._resolve_read_args(*args)

        aggregate_field = max_field or min_field
        if isinstance(args[0], FieldProxy) and aggregate_field is not None:
            raise TypeError(
                "When passing Model.fields, use conditions instead of "
                "max_field/min_field."
            )

        rows = self._fetch_read_rows(
            target_name,
            model_class,
            conditions,
            max_field=max_field,
            min_field=min_field,
            latest=True,
        )
        if not rows:
            return None
        if raw:
            return rows[0]
        return self._hydrate_row(rows[0], model_class)

    def read_objects(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        raw: bool = False,
        max_field: Field | None = None,
        min_field: Field | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list:
        """Read objects from the collection, optionally filtered by conditions.

        Accepts three calling styles:

        1. **Model class** — return all objects of that type::

               col.read_objects(Book)

        2. **Conditions** — return objects matching the given filters.
           The model class is inferred from the conditions::

               col.read_objects(Book.fields.genre == "sci-fi")
               col.read_objects(
                   Book.fields.pages > 300,
                   Book.fields.genre == "sci-fi",
               )

        3. **FieldProxy** (legacy) — ``Model.fields`` alone returns all
           objects, equivalent to passing the model class directly::

               col.read_objects(Book.fields)

        Automatically filters by ``__centauro_name__`` so only objects
        of the requested type are returned. Multiple conditions are
        ANDed together.

        Args:
            *args: A model class, a ``FieldProxy`` (``Model.fields``),
                or one or more ``Condition`` / ``OrGroup`` objects.
            raw: If True, return raw database rows instead of models.
            max_field: Optional field to select rows matching the maximum
                value within the filtered set. Must be ``int``, ``float``,
                ``date``, or ``datetime`` typed.
            min_field: Optional field to select rows matching the minimum
                value within the filtered set. Must be ``int``, ``float``,
                ``date``, or ``datetime`` typed.

        Returns:
            A list of deserialized model instances, or raw rows if
            raw is True.

        Raises:
            TypeError: If no arguments are provided or arguments are
                of incompatible types.
            ValueError: If conditions reference different model classes
                or a field that does not exist on the model.

        Examples:
            >>> col.read_objects(Book)
            [Book(...), Book(...), ...]
            >>> col.read_objects(Book.fields.genre == "sci-fi")
            [Book(genre='sci-fi', ...)]
            >>> col.read_objects(
            ...     Book.fields.pages > 300,
            ...     Book.fields.genre == "sci-fi",
            ... )
            [Book(pages=412, genre='sci-fi', ...)]
        """
        if max_field is not None and min_field is not None:
            raise TypeError("Use only one of max_field or min_field.")
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be >= 0, got {limit}")
        if offset is not None:
            if offset < 0:
                raise ValueError(f"offset must be >= 0, got {offset}")
            if limit is None:
                raise ValueError(
                    "offset requires limit. Pagination without an upper "
                    "bound is rejected to avoid unbounded reads."
                )

        model_class, target_name, conditions = self._resolve_read_args(*args)

        # Style-specific restrictions (backward compatibility)
        first = args[0]
        aggregate_field = max_field or min_field
        if isinstance(first, type) and issubclass(first, CentauroModel):
            if len(args) > 1 and aggregate_field is None:
                raise TypeError(
                    "When passing a model class, no additional arguments "
                    "should follow. Use Model.fields.field_name == value "
                    "to add conditions."
                )
        if isinstance(first, FieldProxy) and aggregate_field is not None:
            raise TypeError(
                "When passing Model.fields, use conditions instead of "
                "max_field/min_field."
            )

        rows = self._fetch_read_rows(
            target_name,
            model_class,
            conditions,
            max_field=max_field,
            min_field=min_field,
            limit=limit,
            offset=offset,
        )

        if raw:
            return rows
        return [self._hydrate_row(row, model_class) for row in rows]

    def count_objects(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
    ) -> int:
        """Return the number of objects matching the given filter.

        Accepts the same calling styles as ``read_objects`` (model
        class, conditions, or ``Model.fields``). Always filters by
        ``__centauro_name__`` so cross-type rows don't leak into the
        count.

        Pairs with ``read_objects(..., limit=, offset=)`` so a client
        can drive its own pager without materialising the full result
        set in memory.
        """
        model_class, target_name, conditions = self._resolve_read_args(*args)
        d = self.conn.dialect
        if not conditions:
            sql = f"SELECT COUNT(*) AS n FROM {self._objects_table} WHERE name = {d.ph}"
            row = self.conn.fetchone(sql, (target_name,))
        else:
            where_sql, params = d._build_where(target_name, conditions)
            sql = f"SELECT COUNT(*) AS n FROM {self._objects_table} WHERE {where_sql}"
            row = self.conn.fetchone(sql, tuple(params))
        if row is None:
            return 0
        return int(row["n"])

    @overload
    def aggregate(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        count: Literal[True],
        sum: None = None,
        avg: None = None,
        group_by: None = None,
    ) -> int: ...

    @overload
    def aggregate(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        count: bool = False,
        sum: Field | None = None,
        avg: Field | None = None,
        group_by: Field | None = None,
    ) -> int | float | dict | pl.DataFrame: ...

    def aggregate(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        count: bool = False,
        sum: Field | None = None,
        avg: Field | None = None,
        group_by: Field | None = None,
    ) -> int | float | dict | pl.DataFrame:
        """Multi-row aggregation entry point.

        v1.0 implements only ``count=True`` (delegates to
        ``count_objects``). ``sum`` / ``avg`` / ``group_by`` are
        reserved keyword arguments and raise ``NotImplementedError``
        until the SQL aggregation layer lands — locking the call
        shape now so client code written today keeps working when
        the rest of the surface ships.

        Args:
            *args: Same calling styles as ``read_objects`` (model
                class, conditions, or ``Model.fields``).
            count: If True, return the matching row count.
            sum: Field to sum. Reserved (NotImplementedError in v1.0).
            avg: Field to average. Reserved.
            group_by: Field to group results by. Reserved.

        Returns:
            The matching row count when ``count=True``. The return
            type is widened to ``int | float | dict | pl.DataFrame``
            to leave room for ``sum`` (int|float), ``avg`` (float),
            and ``group_by`` (dict or DataFrame) without a future
            breaking change. The ``count=True`` overload narrows
            back to ``int`` for callers that opt into precise typing.

        Raises:
            ValueError: If no aggregate is requested.
            NotImplementedError: If ``sum``, ``avg``, or ``group_by``
                is used (or any combination beyond plain ``count``).

        Examples:
            >>> col.aggregate(Book, count=True)
            42
            >>> col.aggregate(Book.fields.genre == "sci-fi", count=True)
            7
        """
        unsupported = []
        if sum is not None:
            unsupported.append("sum")
        if avg is not None:
            unsupported.append("avg")
        if group_by is not None:
            unsupported.append("group_by")
        if unsupported:
            raise NotImplementedError(
                f"aggregate({', '.join(unsupported)}=...) is reserved "
                f"in the v1.0 surface but not yet implemented. The full "
                f"SQL aggregation layer is on the roadmap; today only "
                f"count=True is supported. The signature is stable, so "
                f"call sites written now will keep working when the "
                f"rest ships."
            )
        if not count:
            raise ValueError(
                "aggregate() requires at least one of count=True, "
                "sum=Field, avg=Field, or group_by=Field."
            )
        return self.count_objects(*args)

    def _iter_child_registrations(
        self, parent_class: type[CentauroModel]
    ) -> list[tuple[type[CentauroModel], str, str]]:
        """Return ``(child_cls, field, on_delete)`` tuples registered against
        ``parent_class`` or any of its ancestors, deduped by ``(child_cls, field)``.

        Each child class records itself on the parent class's
        ``__centauro_children__`` list at class-definition time. Subclasses
        of a registered parent inherit the same children pool because
        ``parent_ref(Book)`` covers ``Novel(Book)`` rows too — the row id
        landed in the child's ``meta`` regardless of which subclass instance
        produced it.
        """
        seen: set[tuple[type, str]] = set()
        out: list[tuple[type[CentauroModel], str, str]] = []
        for ancestor in parent_class.__mro__:
            regs = ancestor.__dict__.get("__centauro_children__")
            if not regs:
                continue
            for child_cls, field, on_delete in regs:
                key = (child_cls, field)
                if key in seen:
                    continue
                seen.add(key)
                out.append((child_cls, field, on_delete))
        return out

    def _collect_parent_ids(
        self,
        target_name: str,
        conditions: list[Condition | OrGroup],
    ) -> list[int]:
        """Return the row ids that match a (target_name, conditions) filter."""
        d = self.conn.dialect
        if not conditions:
            sql = f"SELECT id FROM {self._objects_table} WHERE name = {d.ph}"
            rows = self.conn.execute(sql, (target_name,)).fetchall()
        else:
            where_sql, params = d._build_where(target_name, conditions)
            sql = f"SELECT id FROM {self._objects_table} WHERE {where_sql}"
            rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [int(r["id"]) for r in rows]

    def _check_children_before_delete(
        self,
        parent_ids: Sequence[int],
        parent_class: type[CentauroModel],
    ) -> None:
        """Refuse the delete (or raise NIE for reserved modes) if any
        registered child class has rows pointing at ``parent_ids``.

        Called from ``delete_object`` and ``delete_objects`` before the
        DELETE is issued so the parent rows stay put on failure.
        """
        if not parent_ids:
            return
        for child_cls, field, on_delete in self._iter_child_registrations(parent_class):
            if len(parent_ids) == 1:
                cond = Condition(
                    field=field,
                    op="eq",
                    value=int(parent_ids[0]),
                    model_class=child_cls,
                )
            else:
                cond = Condition(
                    field=field,
                    op="in",
                    value=[int(i) for i in parent_ids],
                    model_class=child_cls,
                )
            if self.count_objects(child_cls, cond) == 0:
                continue
            if on_delete == "cascade":
                raise NotImplementedError(
                    f"parent_ref(on_delete='cascade') is reserved but not yet "
                    f"implemented (CentauroDB 0.9.0). The call shape is "
                    f"committed so future releases can implement cascade "
                    f"delete without breaking caller code; see CHANGELOG.md "
                    f"and the project roadmap. For now, delete the children "
                    f"explicitly first: "
                    f"collection.delete_objects({child_cls.__name__}.fields."
                    f"{field} == parent.row.id)."
                )
            if on_delete == "set_null":
                raise NotImplementedError(
                    f"parent_ref(on_delete='set_null') is reserved but not "
                    f"yet implemented (CentauroDB 0.9.0). The call shape is "
                    f"committed so future releases can implement set-null on "
                    f"delete without breaking caller code; see CHANGELOG.md "
                    f"and the project roadmap. For now, manually null the "
                    f"child's '{field}' field before deleting the parent."
                )
            raise ParentHasChildrenError(
                parent_class=parent_class.__name__,
                child_class=child_cls.__name__,
                field=field,
                collection=self.name,
            )

    def delete_object(self, obj: CentauroModel) -> None:
        """Delete an object from the collection.

        Removes the database row and resets the object's persistence
        state so it can be re-written if needed.

        If any child class declares ``parent_ref(type(obj))`` and at
        least one such child row still references this object, the
        delete is refused per the child's ``on_delete`` mode (see
        :func:`~centaurodb.parent_ref`):

        * ``on_delete='restrict'`` (default) — raises
          :class:`~centaurodb.ParentHasChildrenError`; delete the
          children explicitly first.
        * ``on_delete='cascade'`` or ``'set_null'`` — raises
          :class:`NotImplementedError` (reserved, lands in a future
          release).

        Args:
            obj: A persisted CentauroModel instance to delete.

        Raises:
            TypeError: If obj is not a CentauroModel instance.
            ObjectNotPersistedError: If the object has never been
                written to the database.
            ParentHasChildrenError: If any restrict-mode child class
                still references this row.
            NotImplementedError: If a referencing child has
                ``on_delete='cascade'`` or ``'set_null'``.

        Examples:
            >>> col.delete_object(book)
            >>> book._persisted
            False
        """
        if not isinstance(obj, CentauroModel):
            raise TypeError(
                f"Expected a CentauroModel instance, got {type(obj).__name__}"
            )
        if not obj._persisted or obj._row is None:
            raise ObjectNotPersistedError(
                _persistent_name(obj), self.name, self._objects_table
            )

        self._check_children_before_delete([obj._row.id], type(obj))

        sql = self.conn.dialect.delete_object(self._objects_table)
        self.conn.execute(sql, (obj._row.id,))
        self.conn.commit()
        obj._persisted = False
        obj._row = None

    def delete_objects(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
    ) -> int:
        """Delete objects from the collection, optionally filtered by conditions.

        Accepts the same three calling styles as ``read_objects``:

        1. **Model class** — delete all objects of that type::

               col.delete_objects(Book)

        2. **Conditions** — delete objects matching the given filters::

               col.delete_objects(Book.fields.genre == "sci-fi")

        3. **Model class + conditions**::

               col.delete_objects(Book, Book.fields.genre == "sci-fi")

        If any child class declares ``parent_ref`` to the targeted
        model and at least one targeted row has a referencing child,
        the entire delete is refused per the child's ``on_delete`` mode
        (see :meth:`delete_object` for details). The check is atomic at
        the batch level: either every row targeted by the filter is
        deletable, or none are deleted.

        Returns:
            The number of rows deleted.

        Raises:
            TypeError: If no arguments are provided or arguments are
                of incompatible types.
            ValueError: If conditions reference different model classes
                or a field that does not exist on the model.
            ParentHasChildrenError: If any targeted row has restrict-mode
                children.
            NotImplementedError: If any targeted row has cascade- or
                set_null-mode children.

        Examples:
            >>> col.delete_objects(Book)
            3
            >>> col.delete_objects(Book.fields.genre == "sci-fi")
            1
        """
        model_class, target_name, conditions = self._resolve_read_args(*args)
        d = self.conn.dialect

        # Pre-flight: if the parent class has any child registrations,
        # gather the matched ids and refuse if any have referencing
        # children. Skip the SELECT entirely when no child class points
        # at this model.
        if self._iter_child_registrations(model_class):
            parent_ids = self._collect_parent_ids(target_name, conditions)
            self._check_children_before_delete(parent_ids, model_class)

        if not conditions:
            sql = d.delete_all_objects(self._objects_table)
            cursor = self.conn.execute(sql, (target_name,))
        else:
            sql, params = d.delete_objects_by_query(
                self._objects_table, target_name, conditions
            )
            cursor = self.conn.execute(sql, tuple(params))

        count = cursor.rowcount
        self.conn.commit()
        return count

    def refresh(self, obj: CentauroModel) -> None:
        """Re-read the object's metadata and row fields from the database.

        Updates the object in place with the latest values from the
        database row.

        Args:
            obj: A persisted CentauroModel instance to refresh in place.

        Raises:
            ObjectNotPersistedError: If the object has not been written
                to the database.
            ObjectNotFoundError: If the row no longer exists.

        Examples:
            >>> col.refresh(book)
            >>> book.genre  # now reflects the DB state
            'updated_value'
        """
        if not obj._persisted:
            raise ObjectNotPersistedError(
                _persistent_name(obj), self.name, self._objects_table
            )
        if obj._row is None:
            raise ObjectNotPersistedError(
                _persistent_name(obj), self.name, self._objects_table
            )
        sql = self.conn.dialect.select_object(self._objects_table)
        row = self.conn.fetchone(sql, (obj._row.id,))
        if row is None:
            raise ObjectNotFoundError(
                _persistent_name(obj),
                obj._row.id,
                self.name,
                self._objects_table,
            )
        refreshed = _validate_meta(type(obj), row["meta"])
        for field in type(obj).model_fields:
            setattr(obj, field, getattr(refreshed, field))
        obj._row = RowFields(
            id=row["id"],
            name=row["name"],
            write_time=row["write_time"],
            edit_time=row["edit_time"],
        )

    def _link(self, child: CentauroModel, *, to: CentauroModel) -> None:
        """Set the parent reference on a child object.

        Finds the ``parent_ref`` field on the child that points to the
        parent's model class and sets it to ``parent.row.id``.

        Args:
            child: The child object whose parent_ref field will be set.
            to: The parent object. Must be persisted (have a ``row.id``).

        Raises:
            TypeError: If the child has no ``parent_ref`` pointing to the
                parent's model class.
            ObjectNotPersistedError: If the parent has not been written
                to the database.

        Examples:
            >>> col._link(chapter, to=book)
            >>> chapter.book  # now set to book.row.id
            1
        """
        if not to._persisted:
            raise ObjectNotPersistedError(
                _persistent_name(to), self.name, self._objects_table
            )
        if to._row is None:
            raise ObjectNotPersistedError(
                _persistent_name(to), self.name, self._objects_table
            )
        parent_cls = type(to)
        refs = getattr(type(child), "__centauro_refs__", {})
        matching = [f for f, cls in refs.items() if issubclass(parent_cls, cls)]
        if not matching:
            raise TypeError(
                f"{type(child).__name__} has no parent_ref pointing to "
                f"{parent_cls.__name__}."
            )
        if len(matching) > 1:
            raise TypeError(
                f"{type(child).__name__} has multiple parent_ref fields "
                f"pointing to {parent_cls.__name__}: {matching}. "
                f"Set the field directly instead: child.field = parent.row.id"
            )
        setattr(child, matching[0], to._row.id)

    def write_child(
        self,
        child: CentauroModel,
        *,
        parent: CentauroModel,
        duplicate: bool = False,
    ) -> None:
        """Link a child to its parent and write it in one step.

        Convenience method that calls ``_link(child, to=parent)``
        followed by ``write_object(child)``.

        Args:
            child: The child object to link and persist.
            parent: The parent object. Must be persisted.
            duplicate: If True, force insert a new row even if the child
                has already been written.

        Raises:
            TypeError: If the child has no ``parent_ref`` pointing to the
                parent's model class.
            ObjectNotPersistedError: If the parent has not been written.
            ObjectAlreadyPersistedError: If the child was already written
                and ``duplicate`` is False.

        Examples:
            >>> col.write_child(chapter, parent=book)
            >>> chapter.book
            1
            >>> chapter._persisted
            True
        """
        self._link(child, to=parent)
        self.write_object(child, duplicate=duplicate)

    def children_of(
        self,
        parent: CentauroModel,
        child_class: type[T],
        *,
        raw: bool = False,
    ) -> list[T] | list[dict]:
        """Read all children linked to a parent object.

        Auto-discovers the foreign-key field by inspecting
        ``child_class.__centauro_refs__`` for a ``parent_ref`` pointing
        to the parent's model class.

        Args:
            parent: The parent object. Must be persisted.
            child_class: The child ``CentauroModel`` subclass to query.
            raw: If True, return raw database rows instead of models.

        Returns:
            A list of child model instances, or raw rows if ``raw``
            is True.

        Raises:
            TypeError: If the child class has no ``parent_ref`` pointing
                to the parent's model class.
            ObjectNotPersistedError: If the parent has not been written.

        Examples:
            >>> chapters = col.children_of(book, Chapter)
            >>> len(chapters)
            3
        """
        if not parent._persisted:
            raise ObjectNotPersistedError(
                _persistent_name(parent), self.name, self._objects_table
            )
        if parent._row is None:
            raise ObjectNotPersistedError(
                _persistent_name(parent), self.name, self._objects_table
            )
        parent_cls = type(parent)
        refs = getattr(child_class, "__centauro_refs__", {})
        matching = [f for f, cls in refs.items() if issubclass(parent_cls, cls)]
        if not matching:
            raise TypeError(
                f"{child_class.__name__} has no parent_ref pointing to "
                f"{parent_cls.__name__}."
            )
        if len(matching) > 1:
            raise TypeError(
                f"{child_class.__name__} has multiple parent_ref fields "
                f"pointing to {parent_cls.__name__}: {matching}. "
                f"Use read_objects with an explicit condition instead."
            )
        fk_field = matching[0]
        condition = getattr(child_class.fields, fk_field) == parent._row.id
        return self.read_objects(condition, raw=raw)

    _VIEW_FIXED_COLS = {"id", "name", "write_time", "edit_time"}

    def _get_view_columns(self, view_name: str) -> list[str] | None:
        """Return the model-field columns of an existing view, or None.

        Queries the database catalog for the view's column names and
        strips the four fixed columns (``id``, ``name``,
        ``write_time``, ``edit_time``).  Returns ``None`` when the view
        does not exist.
        """
        d = self.conn.dialect
        exists = self.conn.fetchone(d.check_view_exists(), (view_name,))
        if not exists:
            return None
        sql = d.list_view_columns(view_name)
        # SQLite bakes the name into the PRAGMA (no placeholders);
        # Postgres uses a placeholder in the WHERE clause.
        params: tuple[str, ...] = (view_name,) if d.ph in sql else ()
        rows = self.conn.fetchall(sql, params)
        return [r["name"] for r in rows if r["name"] not in self._VIEW_FIXED_COLS]

    def create_view(
        self,
        name: str,
        *models: type[CentauroModel],
        include_fields: list[str] | None = None,
        overwrite: bool = False,
    ) -> None:
        """Create a SQL VIEW that extracts JSON meta fields into columns.

        The view includes ``id``, ``name``, ``write_time``, ``edit_time``
        plus each model field extracted from the JSON meta column. When
        multiple models are given, the view contains the union of all
        fields; missing fields are NULL. Renamed fields use COALESCE.

        Views are virtual — they cannot be indexed directly. To speed up
        queries against a view, create indexes on the underlying objects
        table via ``create_index()``.

        View naming:
            The ``name`` you provide is combined with the collection name
            to form the actual SQL view: ``{collection}_view_{name}``.
            For example, on a collection called ``"research"``::

                col.create_view("dashboard", Sensor, Actuator)
                # → SQL view: research_view_dashboard

            Choose a name that describes **what the view is for** (a use
            case, a consumer, a subset) rather than repeating the model
            name.

        Args:
            name: View name suffix. Must be lowercase alphanumeric with
                underscores, starting with a letter.
            *models: One or more CentauroModel subclasses whose fields
                define the view columns.
            include_fields: Optional list of field names to include in
                the view. When provided, only those fields appear as
                columns (instead of every field on the model). Renamed
                fields listed here still produce the full COALESCE
                expression so old JSON keys are readable. All names must
                exist on at least one of the provided models.
            overwrite: If True, drop and recreate the view if it already
                exists.

        Raises:
            ValueError: If name is invalid, no models are provided, or
                include_fields contains unknown fields.
            ViewAlreadyExistsError: If the view exists and overwrite
                is False.

        Examples:
            >>> col.create_view("all", Book, Ebook, overwrite=True)
            >>> col.create_view("lite", Book, include_fields=["title", "author"])
        """
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(
                f"View name must be lowercase alphanumeric with underscores, got: '{name}'"
            )
        if not models:
            raise ValueError("At least one model class is required")

        # Union of all field names across models, preserving insertion order
        all_fields: dict[str, None] = {}
        for model in models:
            for field_name in model.model_fields:
                all_fields[field_name] = None

        if include_fields is not None:
            unknown_include = set(include_fields) - set(all_fields)
            if unknown_include:
                raise ValueError(
                    f"include_fields {unknown_include} not found in model fields: {set(all_fields)}"
                )
            # Preserve model field order, restricted to the requested subset
            fields: dict[str, None] = {
                f: None for f in all_fields if f in include_fields
            }
        else:
            fields = all_fields

        d = self.conn.dialect

        # Build column expressions, using COALESCE for renamed fields
        col_exprs = []
        for f in fields:
            json_keys = [f]
            # Collect old names from validation_alias (AliasChoices)
            for model in models:
                fi = model.model_fields.get(f)
                if fi and isinstance(fi.validation_alias, AliasChoices):
                    for choice in fi.validation_alias.choices:
                        if isinstance(choice, str) and choice not in json_keys:
                            json_keys.append(choice)
            if len(json_keys) == 1:
                col_exprs.append(d.json_col(f))
            else:
                col_exprs.append(d.coalesce_json_cols(f, json_keys))
        json_cols = ", ".join(col_exprs)
        model_names = ", ".join(f"'{_persistent_name(m)}'" for m in models)
        view_name = f"{self.name}_view_{name}"

        exists = self.conn.fetchone(d.check_view_exists(), (view_name,))
        if exists:
            if not overwrite:
                raise ViewAlreadyExistsError(view_name, self.name)
            self.conn.execute(d.drop_view(view_name))

        self.conn.execute(
            d.create_view(view_name, self._objects_table, json_cols, model_names)
        )

        self.conn.commit()

    def refresh_view(
        self,
        name: str,
        *models: type[CentauroModel],
        include_fields: list[str] | None = None,
    ) -> None:
        """Drop and recreate a view after a schema change.

        Views bake column expressions and model-name filters into their
        SQL definition at creation time. A refresh is only needed when
        the definition becomes stale:

        - A model field was **renamed** (the view must use a COALESCE
          expression to read both old and new JSON keys).
        - A model field was **added or removed** (the view needs a new
          column or should stop exposing the old one).
        - A new **model class** was added to a multi-model view (the
          ``WHERE name IN (...)`` filter must be updated).
        - You want to **change** which columns the view exposes
          (``include_fields``).

        There is **no need** to refresh after writing, updating, or
        deleting rows — views always read live data from the underlying
        table.

        Indexes live on the objects table, not on views. Refreshing a
        view has no effect on indexes. Use ``create_index()`` /
        ``drop_index()`` to manage indexes independently.

        When ``include_fields`` is omitted (the default ``None``), the
        method introspects the existing view in the database catalog and
        reuses the current column subset. Pass an explicit list to
        change it. Renamed fields are translated automatically::

            # Schema change: pick up renamed/new fields, keep existing
            # column subset.
            col.refresh_view("dash", SensorV2)

            # Change the column subset.
            col.refresh_view("dash", SensorV2,
                             include_fields=["temperature", "location"])

        Args:
            name: View name suffix (same as used in ``create_view``).
            *models: One or more CentauroModel subclasses whose fields
                define the view columns.
            include_fields: Field names to include in the view. When
                ``None`` (default), the current columns of the existing
                view are reused. If the view does not exist yet, all
                model fields are included (same as ``create_view``
                default).

        Examples:
            >>> col.refresh_view("all_books", BookV2)
            >>> col.refresh_view("lite", BookV2, include_fields=["title"])
        """
        view_name = f"{self.name}_view_{name}"

        if include_fields is None:
            existing_cols = self._get_view_columns(view_name)
            if existing_cols is not None:
                # Map old/renamed column names to current model field names
                # so that introspected columns survive field renames.
                alias_to_field: dict[str, str] = {}
                for model in models:
                    for field_name, fi in model.model_fields.items():
                        if isinstance(fi.validation_alias, AliasChoices):
                            for choice in fi.validation_alias.choices:
                                if isinstance(choice, str):
                                    alias_to_field[choice] = field_name
                translated = [alias_to_field.get(c, c) for c in existing_cols]
                # Deduplicate while preserving order (multiple old names
                # may map to the same current field).
                seen: set[str] = set()
                deduped: list[str] = []
                for f in translated:
                    if f not in seen:
                        seen.add(f)
                        deduped.append(f)
                include_fields = deduped

        self.create_view(
            name,
            *models,
            include_fields=include_fields,
            overwrite=True,
        )

    @staticmethod
    def _validate_field_name(field: str) -> None:
        """Raise ValueError if *field* is not a safe identifier."""
        if not re.fullmatch(r"[a-z][a-z0-9_]*", field):
            raise ValueError(
                f"Field name must be lowercase alphanumeric with underscores, got: '{field}'"
            )

    def create_index(self, field: str) -> None:
        """Create an index on the objects table. Idempotent — safe to call on every startup.

        Indexes live on the ``{name}_objects`` table and speed up queries
        against both the table and any views built on top of it.

        Automatically selects the correct index type:

        - **Plain column index** for ``id``, ``name``, ``write_time``,
          ``edit_time``.
        - **JSON expression index** for any other name (field stored in
          the ``meta`` JSON blob).

        The index is named ``idx_{collection}_{field}``. If the index
        already exists it is silently left unchanged.

        Args:
            field: Field name to index. Must be a standard column name
                or a field that exists in the ``meta`` JSON blob.

        Raises:
            ValueError: If field contains invalid characters.

        Examples:
            >>> col.create_index("status")       # JSON expression index
            >>> col.create_index("write_time")   # plain column index
        """
        self._validate_field_name(field)
        d = self.conn.dialect
        if field in _PLAIN_COLUMNS:
            self.conn.execute(
                d.create_plain_index(self.name, self._objects_table, field)
            )
        else:
            self.conn.execute(
                d.create_expression_index(self.name, self._objects_table, field)
            )
        self.conn.commit()

    def drop_index(self, field: str) -> None:
        """Drop an index by field name. Silent if the index does not exist.

        Resolves the index name as ``idx_{collection}_{field}`` and issues
        ``DROP INDEX IF EXISTS``.

        Args:
            field: The field name used when the index was created.

        Raises:
            ValueError: If ``field`` refers to an auto-managed index that
                CentauroDB requires for correct operation (e.g. ``"name"``).

        Examples:
            >>> col.drop_index("status")
            >>> col.drop_index("old_field_name")  # no-op if already gone
        """
        self._validate_field_name(field)
        if field in _MANAGED_FIELDS:
            raise ValueError(
                f"The '{field}' index is auto-managed by CentauroDB and "
                f"cannot be dropped. It is required for correct collection operation."
            )
        index_name = f"idx_{self.name}_{field}"
        self.conn.execute(self.conn.dialect.drop_index(index_name))
        self.conn.commit()

    def list_indexes(self, *, raw: bool = False) -> list[str]:
        """List user-created indexes on this collection's objects table.

        Always excludes the auto-managed ``idx_{collection}_name`` index.

        Args:
            raw: If True, return full index names (e.g.
                ``"idx_library_genre"``). If False (default), return only
                the field-name suffix (e.g. ``"genre"``).

        Returns:
            A list of index identifiers sorted alphabetically.

        Examples:
            >>> col.list_indexes()
            ['status', 'write_time']
            >>> col.list_indexes(raw=True)
            ['idx_library_genre', 'idx_library_write_time']
        """
        auto_index = f"idx_{self.name}_name"
        rows = self.conn.fetchall(
            self.conn.dialect.list_indexes(), (self._objects_table,)
        )
        names = sorted(r["name"] for r in rows if r["name"] != auto_index)
        if raw:
            return names
        prefix = f"idx_{self.name}_"
        return [n[len(prefix) :] if n.startswith(prefix) else n for n in names]

    def sql_select(self, sql: str, params: tuple = ()):
        """Run a raw SELECT and return a Polars DataFrame.

        Use this to query views, the objects table, or any other table
        in the database with full SQL flexibility (aggregations, joins,
        GROUP BY, etc.).  Delegates to ``Engine.sql_select``.

        Args:
            sql: A SELECT (or WITH … SELECT) SQL string.
            params: Bind parameters for the query.

        Returns:
            A ``polars.DataFrame`` with one column per selected column.

        Raises:
            ValueError: If the statement is not a SELECT / WITH query.
            ImportError: If polars is not installed.

        Examples:
            >>> col.create_view("dash", Sensor)
            >>> col.sql_select("SELECT * FROM sensors_view_dash WHERE temp > ?", (20,))
            >>> col.sql_select(
            ...     "SELECT unit, COUNT(*) as n FROM sensors_view_dash GROUP BY unit"
            ... )
        """
        return self.conn.sql_select(sql, params)

    def describe_views(self) -> dict[str, list[str]]:
        """List all views and their column names for this collection.

        Returns:
            A dict mapping view names to lists of model-field column
            names (excluding the fixed columns ``id``, ``name``,
            ``write_time``, ``edit_time``).

        Examples:
            >>> col.describe_views()
            {'library_view_all_books': ['title', 'genre']}
        """
        d = self.conn.dialect
        view_prefix = f"{self.name}_view_"
        views = self.conn.fetchall(d.list_views(), (f"{view_prefix}%",))

        result: dict[str, list[str]] = {}
        for view_row in views:
            view_name = view_row["name"]
            view_cols = self._get_view_columns(view_name)
            result[view_name] = view_cols if view_cols is not None else []

        return result


class Collection(BaseCollection):
    """A collection for storing objects without time-series values.

    Rejects ``CentauroModelSeries`` instances — use
    ``TimeSeriesCollection`` for those.

    Args:
        conn: An Engine instance.
        name: Collection name.

    Examples:
        >>> conn = Engine()
        >>> col = Collection(conn, "library")
        >>> col.write_object(Book(title="Dune", genre="sci-fi"))
    """

    def write_object(self, obj: CentauroModel, *, duplicate: bool = False) -> None:
        """Insert an object into the collection.

        Args:
            obj: A CentauroModel instance to persist. Must not be a
                CentauroModelSeries.
            duplicate: If True, insert a new row even if the object has
                already been persisted.

        Raises:
            TypeError: If obj is a CentauroModelSeries.

        Examples:
            >>> col.write_object(Book(title="Dune", genre="sci-fi"))
        """
        if isinstance(obj, CentauroModelSeries):
            raise TypeError(
                f"Collection does not accept CentauroModelSeries instances. "
                f"Use TimeSeriesCollection to write {type(obj).__name__}."
            )
        self._write_object_no_commit(obj, duplicate=duplicate)
        self.conn.commit()

    def write_objects(
        self, objs: Sequence[CentauroModel], *, duplicate: bool = False
    ) -> None:
        """Insert multiple objects in a single atomic transaction.

        Validates that none of the objects are ``CentauroModelSeries``
        instances, then delegates to the base class batch write.

        Args:
            objs: A sequence of CentauroModel instances to persist.
            duplicate: If True, force insert new rows even for objects
                that have already been written.

        Raises:
            TypeError: If any element is a CentauroModelSeries.
            ObjectAlreadyPersistedError: If any object has already been
                written and duplicate is False.

        Examples:
            >>> books = [Book(title="Dune"), Book(title="Foundation")]
            >>> col.write_objects(books)
        """
        for obj in objs:
            if isinstance(obj, CentauroModelSeries):
                raise TypeError(
                    f"Collection does not accept CentauroModelSeries instances. "
                    f"Use TimeSeriesCollection to write {type(obj).__name__}."
                )
        super().write_objects(objs, duplicate=duplicate)


class TimeSeriesCollection(BaseCollection):
    """A collection for storing objects with associated time-series values.

    Creates both ``{name}_objects`` and ``{name}_values`` tables.

    Args:
        conn: An Engine instance.
        name: Collection name.

    Examples:
        >>> conn = Engine()
        >>> col = TimeSeriesCollection(conn, "metrics")
        >>> col.write_object(metric)
    """

    def __init__(self, conn: Engine, name: str):
        super().__init__(conn, name)
        self._values_table = f"{name}_values"
        self._create_values_table()

    def _create_values_table(self):
        """Create the values table if it doesn't exist."""
        d = self.conn.dialect
        self.conn.execute(
            d.create_values_table(self._values_table, self._objects_table)
        )
        self.conn.commit()

    def write_object(self, obj: CentauroModel, *, duplicate: bool = False) -> None:
        """Insert an object and its values into the collection.

        Persists the object row, then inserts all ``obj.values`` into
        the values table.

        Args:
            obj: A CentauroModelSeries instance to persist.
            duplicate: If True, insert a new row even if the object has
                already been persisted.

        Raises:
            TypeError: If obj is not a CentauroModelSeries.

        Examples:
            >>> metric = Metric(unit="kg", values=[
            ...     CentauroValues(time="2024-01-01", value=1.0),
            ... ])
            >>> col.write_object(metric)
        """
        self._write_object_no_commit(obj, duplicate=duplicate)
        self.conn.commit()

    def _write_object_no_commit(
        self, obj: CentauroModel, *, duplicate: bool = False
    ) -> None:
        """Insert an object and its values without committing.

        Validates that the object is a ``CentauroModelSeries`` and
        inserts both the object row and its values rows, but does
        **not** call ``commit()``.

        Args:
            obj: A CentauroModelSeries instance to persist.
            duplicate: If True, force insert a new row even if the
                object has already been written.

        Raises:
            TypeError: If obj is not a CentauroModelSeries.
        """
        if not isinstance(obj, CentauroModelSeries):
            raise TypeError(
                f"TimeSeriesCollection requires CentauroModelSeries instances. "
                f"Use Collection to write {type(obj).__name__}."
            )
        super()._write_object_no_commit(obj, duplicate=duplicate)
        if isinstance(obj, CentauroModelSeries) and obj.values:
            if obj._row is None:
                raise ObjectNotPersistedError(
                    _persistent_name(obj), self.name, self._objects_table
                )
            now = datetime.now(timezone.utc).isoformat()
            d = self.conn.dialect
            self.conn.executemany(
                d.insert_values(self._values_table),
                [(obj._row.id, v.time, v.value, now) for v in obj.values],
            )

    def write_objects(
        self, objs: Sequence[CentauroModel], *, duplicate: bool = False
    ) -> None:
        """Insert multiple time-series objects in a single atomic transaction.

        All objects are validated as ``CentauroModelSeries`` instances
        and inserted (both object rows and their values) within one
        transaction. If any object fails validation, **none** of the
        objects are persisted.

        Args:
            objs: A sequence of CentauroModelSeries instances to persist.
            duplicate: If True, force insert new rows even for objects
                that have already been written.

        Raises:
            TypeError: If any element is not a CentauroModelSeries.
            ObjectAlreadyPersistedError: If any object has already been
                written and duplicate is False.

        Examples:
            >>> metrics = [Metric(unit="kg"), Metric(unit="m")]
            >>> col.write_objects(metrics)
            >>> all(m._persisted for m in metrics)
            True
        """
        if not objs:
            return
        saved = [
            (obj._persisted, obj._row) for obj in objs if isinstance(obj, CentauroModel)
        ]
        try:
            for obj in objs:
                self._write_object_no_commit(obj, duplicate=duplicate)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            cm_objs = [obj for obj in objs if isinstance(obj, CentauroModel)]
            for obj, (was_persisted, old_row) in zip(cm_objs, saved):
                obj._persisted = was_persisted
                obj._row = old_row
            raise

    def read_object_by_id(
        self,
        id: int,
        model_class: type[T],
        *,
        raw: bool = False,
        hydrate_values: bool = True,
    ) -> T | RowProtocol:
        """Read an object and optionally its values from the collection.

        Args:
            id: The row id of the object to read.
            model_class: The CentauroModel subclass to deserialize into.
            raw: If True, return the raw row instead of a model.
            hydrate_values: If True (default), populate ``obj.values``
                from the values table. Set to False to skip hydration.

        Returns:
            The deserialized model instance, or a dict-like row if
            raw is True.

        Raises:
            ObjectNotFoundError: If no object with the given id exists.

        Examples:
            >>> metric = col.read_object_by_id(1, Metric)
            >>> len(metric.values)
            5
            >>> light = col.read_object_by_id(1, Metric, hydrate_values=False)
            >>> light.values
            []
        """
        obj = super().read_object_by_id(id, model_class, raw=raw)
        if raw:
            return obj
        if isinstance(obj, CentauroModelSeries):
            if hydrate_values:
                self._hydrate_values(obj)
            else:
                obj._values_hydrated = False
        return obj

    def read_latest_object(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        raw: bool = False,
        hydrate_values: bool = True,
        max_field: Field | None = None,
        min_field: Field | None = None,
    ) -> CentauroModel | dict | None:
        """Return the most recently edited object, optionally filtered and with values.

        Delegates to ``BaseCollection.read_latest_object`` then hydrates
        time-series values when the result is a ``CentauroModelSeries``.

        Args:
            *args: A model class, ``FieldProxy``, or conditions.
            raw: If True, return the raw database row.
            hydrate_values: If True (default), populate ``obj.values``
                from the values table. Set to False to skip hydration.
            max_field: Narrow to rows with the maximum value of this field.
            min_field: Narrow to rows with the minimum value of this field.

        Returns:
            The most recently edited model instance, a dict-like row
            if ``raw`` is True, or ``None`` if no objects exist.

        Examples:
            >>> latest = col.read_latest_object(Metric)
            >>> len(latest.values)
            5
            >>> light = col.read_latest_object(Metric, hydrate_values=False)
            >>> light.values
            []
        """
        obj = super().read_latest_object(
            *args, raw=raw, max_field=max_field, min_field=min_field
        )
        if obj is None or raw:
            return obj
        if isinstance(obj, CentauroModelSeries):
            if hydrate_values:
                self._hydrate_values(obj)
            else:
                obj._values_hydrated = False
        return obj

    def read_objects(
        self,
        *args: type[CentauroModel] | FieldProxy | Condition | OrGroup,
        raw: bool = False,
        hydrate_values: bool = True,
        max_field: Field | None = None,
        min_field: Field | None = None,
    ) -> list:
        """Read objects, optionally filtered, and hydrate time-series values.

        Delegates to ``BaseCollection.read_objects`` and then populates
        ``obj.values`` from the values table for each result.

        Args:
            *args: A model class, ``FieldProxy``, or conditions.
            raw: If True, return raw database rows instead of models.
            hydrate_values: If True (default), populate ``obj.values``
                from the values table. Set to False to skip hydration.

        Returns:
            A list of deserialized model instances, or raw rows if
            raw is True.

        Examples:
            >>> col.read_objects(Metric)
            [Metric(values=[...]), ...]
            >>> col.read_objects(Metric, hydrate_values=False)
            [Metric(values=[], ...), ...]
        """
        results = super().read_objects(
            *args, raw=raw, max_field=max_field, min_field=min_field
        )
        if raw:
            return results
        for obj in results:
            if isinstance(obj, CentauroModelSeries):
                if hydrate_values:
                    self._hydrate_values(obj)
                else:
                    obj._values_hydrated = False
        return results

    def update_object(
        self,
        obj: CentauroModel,
        *,
        values_mode: Literal["replace", "append", "upsert"] = "append",
    ) -> None:
        """Update an existing object and its values in the collection.

        Updates the object meta and ``edit_time``, then handles
        ``obj.values`` according to ``values_mode``.

        Args:
            obj: A persisted CentauroModelSeries instance.
            values_mode: How to handle existing time-series points:

                - ``append`` (default): add new timestamps, keep
                  existing ones untouched.
                - ``upsert``: add new timestamps, overwrite the value
                  at timestamps that already exist.
                - ``replace``: delete the entire series and write
                  ``obj.values`` from scratch.

        Raises:
            TypeError: If obj is not a CentauroModel instance.
            ObjectNotPersistedError: If the object has never been
                written to the database.

        Examples:
            >>> metric.values.append(CentauroValues(time="2024-02-01", value=3.0))
            >>> col.update_object(metric, values_mode="append")
        """
        _VALID_VALUES_MODES = {"replace", "append", "upsert"}
        if values_mode not in _VALID_VALUES_MODES:
            raise ValueError(
                f"Invalid values_mode '{values_mode}'. "
                f"Must be one of: {', '.join(sorted(_VALID_VALUES_MODES))}"
            )

        row = getattr(obj, "_row", None)
        saved_edit_time = row.edit_time if row is not None else None
        try:
            self._update_object_no_commit(obj)
            if isinstance(obj, CentauroModelSeries):
                if obj._row is None:
                    raise ObjectNotPersistedError(
                        _persistent_name(obj), self.name, self._objects_table
                    )
                d = self.conn.dialect
                if values_mode == "replace":
                    self.conn.execute(
                        d.delete_values(self._values_table), (obj._row.id,)
                    )
                    if obj.values:
                        now = datetime.now(timezone.utc).isoformat()
                        rows = [(obj._row.id, v.time, v.value, now) for v in obj.values]
                        self.conn.executemany(d.insert_values(self._values_table), rows)
                elif obj.values:
                    now = datetime.now(timezone.utc).isoformat()
                    rows = [(obj._row.id, v.time, v.value, now) for v in obj.values]
                    if values_mode == "append":
                        self.conn.executemany(
                            d.insert_or_ignore_values(self._values_table), rows
                        )
                    elif values_mode == "upsert":
                        self.conn.executemany(d.upsert_values(self._values_table), rows)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            if row is not None and saved_edit_time is not None:
                row.edit_time = saved_edit_time
            raise

    def _hydrate_values(self, obj: CentauroModelSeries) -> None:
        """Populate ``obj.values`` from the values table."""
        d = self.conn.dialect
        if obj._row is None:
            raise ObjectNotPersistedError(
                _persistent_name(obj), self.name, self._objects_table
            )
        rows = self.conn.fetchall(d.select_values(self._values_table), (obj._row.id,))
        obj.values = [CentauroValues(time=r["time"], value=r["value"]) for r in rows]
        obj._values_hydrated = True

    def refresh(self, obj: CentauroModel) -> None:
        """Re-read the object's metadata, row fields, and values.

        Args:
            obj: A persisted CentauroModelSeries instance to refresh.

        Raises:
            ObjectNotPersistedError: If the object has not been written.
            ObjectNotFoundError: If the row no longer exists.

        Examples:
            >>> col.refresh(metric)
            >>> metric.values  # re-loaded from DB
            [CentauroValues(...), ...]
        """
        super().refresh(obj)
        if isinstance(obj, CentauroModelSeries):
            self._hydrate_values(obj)
