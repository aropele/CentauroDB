import re
from datetime import datetime
from typing import Any, ClassVar, Literal, Self

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    TypeAdapter,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo

from centaurodb.exceptions import ValuesNotHydratedError
from centaurodb.query import FieldAccessor

_CENTAURO_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

OnDeleteMode = Literal["restrict", "cascade", "set_null"]
_ON_DELETE_VALUES: tuple[str, ...] = ("restrict", "cascade", "set_null")


class _ParentRefMarker:
    """Internal marker stored in ``field_info.metadata`` by ``parent_ref()``."""

    __slots__ = ("parent_model", "on_delete")

    def __init__(
        self,
        parent_model: type,
        on_delete: OnDeleteMode = "restrict",
    ) -> None:
        if on_delete not in _ON_DELETE_VALUES:
            raise ValueError(
                f"parent_ref(on_delete=...) must be one of "
                f"{_ON_DELETE_VALUES!r}, got {on_delete!r}."
            )
        self.parent_model = parent_model
        self.on_delete = on_delete


class RowFields(BaseModel):
    """Raw SQLite row columns (excluding meta)."""

    id: int
    name: str
    write_time: datetime
    edit_time: datetime


class CentauroModel(BaseModel):
    """Base model for all objects stored in CentauroDB collections.

    User-defined models must inherit from this class to be accepted
    by Collection.write_object() and Collection.read_object().

    Backward compatibility
    ----------------------
    CentauroDB stores model data as JSON blobs and avoids traditional
    migrations. Two guardrails are enforced at class-definition time
    to keep stored data readable across schema changes:

    1. **All fields must have a default value.** Adding a new field to a
       model would break deserialization of old rows that lack it, so
       required fields are rejected with a ``TypeError``. Always provide
       a default or use ``Optional``::

           class Book(CentauroModel):
               title: str = ""               # OK
               pages: int | None = None      # OK
               rating: float                 # TypeError at class definition

    3. **Safe field renames via ``renamed_from()``.** When renaming a field,
       old rows still have the old JSON key. Use the ``renamed_from``
       helper so Pydantic reads both old and new names, while always
       writing the new one::

           class Book(CentauroModel):
               # "pg" was renamed to "pages"
               pages: int = renamed_from("pg", default=0)

       This is equivalent to using Pydantic's ``AliasChoices`` with
       ``validation_alias``.

    2. **``extra='forbid'`` is not allowed.** When a field is removed from
       a model, old rows still contain it in their JSON. With
       ``extra='forbid'``, Pydantic would raise ``ValidationError`` on
       those rows. CentauroDB requires ``extra='ignore'`` (the default)
       or ``extra='allow'`` so that obsolete keys are silently discarded::

           class Bad(CentauroModel):
               model_config = ConfigDict(extra="forbid")  # TypeError

    4. **``__centauro_name__`` is mandatory.** Every subclass must set a
       ``__centauro_name__`` class variable. This string is stored in the
       database ``name`` column and is used to filter rows in views.
       Without it, renaming a Python class silently changes the stored
       name, which causes views to miss old rows and name-based queries
       to return incomplete results — with no error or warning. By
       requiring an explicit, stable name you decouple the database
       identity from the Python class name::

           class Book(CentauroModel):
               __centauro_name__ = "Book"     # required
               title: str = ""

           # Later, safely rename the class without affecting stored data:
           class BookRecord(CentauroModel):
               __centauro_name__ = "Book"     # DB rows still match
               title: str = ""

    Relationships
    -------------
    CentauroDB offers two ways to represent related data:

    **Use ``parent_ref`` when:**

    - The child entity has its own lifecycle — it can be created, updated,
      queried, or deleted independently of the parent.
    - You need to query children without loading the parent
      (``children_of``, ``read_objects`` with a filter on the FK field).
    - The relationship is one-to-many and the child set can grow over
      time (e.g. Book → Chapters, Order → LineItems).
    - You want the child visible in views with its own indexed columns.

    **Use nested Pydantic models (plain fields) when:**

    - The nested data has no meaning outside the parent — it is always
      loaded, saved, and deleted together with it.
    - You never need to query or filter on the nested objects
      independently (they live inside the parent's JSON blob).
    - The data is bounded and small (e.g. an ``Address`` inside a
      ``Customer``, a list of ``Tag`` inside a ``Note``).
    - You want atomic reads/writes — one ``read_object`` call returns
      the complete aggregate.

    **Rules of thumb:**

    - If you'd put a foreign key in SQL, use ``parent_ref``.
    - If you'd embed a sub-document in MongoDB, use a nested model.
    - If you're unsure, start nested — it's simpler. Promote to
      ``parent_ref`` only when you need independent queries or the
      child set becomes large.

    Validation
    ----------
    By default, field values are validated every time an attribute is
    assigned (``validate_assignment=True``). This ensures data integrity
    but may add overhead in performance-critical code. To disable this
    behavior, override ``model_config`` in your subclass::

        class MyBook(CentauroModel):
            __centauro_name__ = "MyBook"
            model_config = ConfigDict(validate_assignment=False)
            pages: int = 0
    """

    model_config = ConfigDict(validate_assignment=True, populate_by_name=True)

    __centauro_name__: ClassVar[str]
    __centauro_refs__: ClassVar[dict[str, type["CentauroModel"]]]
    fields: ClassVar[FieldAccessor[Self]] = FieldAccessor()

    _row: RowFields | None = PrivateAttr(default=None)
    _persisted: bool = PrivateAttr(default=False)

    @model_validator(mode="before")
    @classmethod
    def _resolve_parent_refs(cls, data: Any) -> Any:
        """Coerce ``CentauroModel`` instances in parent_ref fields to ``row.id``.

        Allows passing a persisted parent object directly at construction
        time instead of manually extracting its id::

            chapter = Chapter(heading="Part I", book=book)
            # equivalent to: Chapter(heading="Part I", book=book.row.id)

        Args:
            data: The raw input data (dict from kwargs or other).

        Returns:
            The data with parent refs resolved to integer ids.

        Raises:
            ValueError: If the parent object has not been persisted.
        """
        if not isinstance(data, dict):
            return data
        refs = getattr(cls, "__centauro_refs__", {})
        for field_name in refs:
            val = data.get(field_name)
            if isinstance(val, CentauroModel):
                if not val._persisted or val._row is None:
                    raise ValueError(
                        f"Cannot use unpersisted {type(val).__name__} as "
                        f"parent for '{field_name}'. Write the parent to "
                        f"the database first."
                    )
                data[field_name] = val._row.id
        return data

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if cls.__name__ != "CentauroModelSeries" and not getattr(
            cls, "__centauro_name__", None
        ):
            raise TypeError(
                f"CentauroModel '{cls.__name__}' must define a "
                f"__centauro_name__ class variable. This stable identifier "
                f"is stored in the database and decouples the DB identity "
                f"from the Python class name. Example: "
                f'__centauro_name__ = "{cls.__name__}"'
            )
        centauro_name = getattr(cls, "__centauro_name__", None)
        if centauro_name is not None and not _CENTAURO_NAME_RE.match(centauro_name):
            raise TypeError(
                f"CentauroModel '{cls.__name__}' has invalid "
                f"__centauro_name__ {centauro_name!r}: must match "
                f"[A-Za-z_][A-Za-z0-9_]* (identifier shape). The "
                f"persistent name is interpolated into SQL, so anything "
                f"outside this whitelist is rejected to close injection "
                f"vectors."
            )
        if cls.model_config.get("extra") == "forbid":
            raise TypeError(
                f"CentauroModel '{cls.__name__}' cannot use extra='forbid' "
                f"as it breaks backward compatibility when fields are "
                f"removed from the model."
            )
        for name, field_info in cls.model_fields.items():
            if field_info.is_required():
                raise TypeError(
                    f"CentauroModel field '{cls.__name__}.{name}' must have a "
                    f"default value to preserve backward compatibility with "
                    f"stored data. Use Optional with None default or provide "
                    f"a sensible default value."
                )

        # Register parent_ref relationships
        refs: dict[str, type[CentauroModel]] = {}
        ref_markers: dict[str, _ParentRefMarker] = {}
        for name, field_info in cls.model_fields.items():
            for m in field_info.metadata:
                if isinstance(m, _ParentRefMarker):
                    refs[name] = m.parent_model
                    ref_markers[name] = m
                    # Reverse-index this child class on the parent class so
                    # delete_object(parent) can find rows that depend on it.
                    # Each registered class owns its own list (not inherited),
                    # so unrelated peers don't share a children registry.
                    parent_cls = m.parent_model
                    if "__centauro_children__" not in parent_cls.__dict__:
                        parent_cls.__centauro_children__ = []
                    parent_cls.__centauro_children__.append((cls, name, m.on_delete))
                    break
        cls.__centauro_refs__ = refs
        cls.__centauro_ref_markers__ = ref_markers

    def __setattr__(self, name: str, value: Any) -> None:
        """Resolve ``CentauroModel`` instances to ``row.id`` on assignment.

        Ensures ``chapter.book = book`` behaves the same as
        ``Chapter(book=book)`` — the object is coerced to its
        integer ``row.id`` before Pydantic's type validation runs.

        Args:
            name: The attribute name.
            value: The value to set.

        Raises:
            ValueError: If the value is an unpersisted ``CentauroModel``.
        """
        refs = getattr(type(self), "__centauro_refs__", {})
        if name in refs and isinstance(value, CentauroModel):
            if not value._persisted or value._row is None:
                raise ValueError(
                    f"Cannot use unpersisted {type(value).__name__} as "
                    f"parent for '{name}'. Write the parent to "
                    f"the database first."
                )
            value = value._row.id
        super().__setattr__(name, value)

    @property
    def row(self) -> RowFields:
        """Access row columns (id, name, write_time, edit_time).

        Returns:
            The RowFields instance with database metadata.

        Raises:
            AttributeError: If the object has not been written to or read
                from the database.
        """
        if self._row is None:
            raise AttributeError(
                "Row metadata is not available. "
                "This object has not been written to or read from the database."
            )
        return self._row


def parent_ref(
    parent_model: type[CentauroModel],
    *,
    on_delete: OnDeleteMode = "restrict",
    **field_kwargs: Any,
) -> FieldInfo:
    """Declare a one-to-many relationship to a parent model.

    Creates a nullable ``int`` field that stores the parent object's
    ``row.id``. The relationship metadata is registered on the child
    class so that collection helpers (``link``, ``write_child``,
    ``children_of``) can auto-discover the foreign-key field.

    The field defaults to ``None`` to satisfy CentauroDB's zero-migration
    guardrail (all fields must have defaults). However, the collection
    enforces non-null at write time — an object with an unset parent ref
    cannot be persisted.

    Args:
        parent_model: The parent ``CentauroModel`` subclass this field
            points to.
        on_delete: How ``Collection.delete_object`` / ``delete_objects``
            behave when a row of ``parent_model`` is deleted while one
            or more child rows still reference it. One of:

            * ``"restrict"`` (default) — the delete is refused and
              ``ParentHasChildrenError`` is raised. The caller must
              explicitly delete the children first (typically via
              ``collection.delete_objects(child_class.fields.<field> ==
              parent.row.id)``) and then re-issue the parent delete.
            * ``"cascade"`` — reserved. The class definition succeeds,
              but a delete on a parent with matching children raises
              ``NotImplementedError`` pointing at the roadmap. The call
              shape is committed so future releases can implement
              cascade-delete without breaking caller code.
            * ``"set_null"`` — reserved with identical semantics.

            This is a pre-1.0 behaviour change vs CentauroDB 0.5.0,
            where the same delete silently left orphan children. The
            CHANGELOG ``[0.9.0]`` entry documents the break.
        **field_kwargs: Extra keyword arguments forwarded to
            ``pydantic.Field`` (e.g. ``description``).

    Returns:
        A ``pydantic.Field`` configured as ``int | None`` with
        ``default=None`` and relationship metadata attached via
        ``json_schema_extra``.

    Raises:
        TypeError: If ``parent_model`` is not a ``CentauroModel``
            subclass.
        ValueError: If ``on_delete`` is not one of the three legal
            values (defensive runtime check for callers that bypass
            type-checking).

    Examples:
        >>> class Book(CentauroModel):
        ...     __centauro_name__ = "Book"
        ...     title: str = ""
        ...
        >>> class Chapter(CentauroModel):
        ...     __centauro_name__ = "Chapter"
        ...     book: int | None = parent_ref(Book)
        ...     heading: str = ""
    """
    if not (isinstance(parent_model, type) and issubclass(parent_model, CentauroModel)):
        raise TypeError(
            f"parent_ref() expects a CentauroModel subclass, got {parent_model!r}"
        )
    field_kwargs.setdefault("default", None)
    f = Field(**field_kwargs)
    f.metadata.append(_ParentRefMarker(parent_model, on_delete=on_delete))
    return f


def renamed_from(*old_names: str, **field_kwargs: Any) -> FieldInfo:
    """Create a field that reads from old JSON key names for backward compatibility.

    When a field is renamed, old rows in the database still contain the
    previous key. This helper sets up a ``validation_alias`` so Pydantic
    accepts both old and new names during deserialization, while always
    serializing under the current field name.

    Args:
        *old_names: One or more previous JSON key names for this field.
        **field_kwargs: Passed through to ``pydantic.Field`` (e.g.
            ``default``, ``description``).

    Returns:
        A ``pydantic.Field`` with the appropriate ``validation_alias``.

    Example::

        class Book(CentauroModel):
            # "pg" was renamed to "pages"
            pages: int = renamed_from("pg", default=0)

    If a field is renamed more than once, list all previous names so
    that rows from every era can still be read::

        class Book(CentauroModel):
            # v1: "pg", v2: "pages", v3: "page_count"
            page_count: int = renamed_from("pg", "pages", default=0)
    """
    return Field(validation_alias=AliasChoices(*old_names), **field_kwargs)


class CentauroValues(BaseModel):
    """A single time-series data point."""

    time: datetime
    value: float


_values_list_adapter = TypeAdapter(list[CentauroValues])


class CentauroModelSeries(CentauroModel):
    """Base model for objects with a single associated time-series.

    Each ``CentauroModelSeries`` instance stores exactly **one** series of
    ``(time, value)`` pairs. If you need to track multiple series (e.g.
    temperature *and* humidity), create a separate subclass and object for
    each.

    The ``values`` field holds time-series data points that are persisted
    in the values table, not in the JSON meta column. You can populate it
    with a ``list[CentauroValues]`` or a polars DataFrame with ``time``
    and ``value`` columns::

        import polars as pl

        df = pl.DataFrame({
            "time": [datetime(2025, 1, 1), datetime(2025, 1, 2)],
            "value": [20.5, 21.0],
        })
        obj = MySensor(values=df)
    """

    values: list[CentauroValues] = Field(default_factory=list, exclude=True)
    _values_hydrated: bool = PrivateAttr(default=True)

    @field_validator("values", mode="wrap")
    @classmethod
    def _coerce_dataframe(cls, v: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        try:
            import polars as pl
        except ImportError:
            return handler(v)
        if isinstance(v, pl.DataFrame):
            return _values_list_adapter.validate_python(v.to_dicts())
        return handler(v)

    @property
    def df(self):
        """Return values as a polars DataFrame.

        Returns:
            A polars DataFrame with columns ``time`` and ``value``.

        Raises:
            ValuesNotHydratedError: If the object was read with
                ``hydrate_values=False`` and values have not been
                hydrated yet. Call ``collection.refresh(obj)``
                before accessing this property.
            ImportError: If polars is not installed.
        """
        if not self._values_hydrated:
            raise ValuesNotHydratedError(
                "Values have not been hydrated. "
                "Either read the object with hydrate_values=True "
                "(the default), or call collection.refresh(obj) "
                "before accessing .df"
            )
        try:
            import polars as pl
        except ImportError:
            raise ImportError(
                "polars is required for .df — install it with: "
                "pip install centaurodb[polars]"
            )
        schema = {"time": pl.Datetime, "value": pl.Float64}
        return pl.DataFrame(
            {
                "time": [v.time for v in self.values],
                "value": [v.value for v in self.values],
            },
            schema=schema,
        )
