class ValuesNotHydratedError(Exception):
    """Raised when accessing values that have not been hydrated from the database."""


class ObjectNotFoundError(Exception):
    """Raised when a requested object does not exist in the collection."""

    def __init__(self, model_name: str, id: int, collection: str, table: str):
        """Initialize ObjectNotFoundError.

        Args:
            model_name: Name of the model class that was requested.
            id: The row id that was not found.
            collection: The collection name.
            table: The underlying database table name.
        """
        self.model_name = model_name
        self.id = id
        self.collection = collection
        self.table = table
        super().__init__(
            f"{model_name} with id={id} not found "
            f"in collection '{collection}' (table '{table}')."
        )


class ObjectAlreadyPersistedError(Exception):
    """Raised when write_object() is called on an already persisted object."""

    def __init__(self, model_name: str, id: int, collection: str, table: str):
        """Initialize ObjectAlreadyPersistedError.

        Args:
            model_name: Name of the model class.
            id: The row id of the already persisted object.
            collection: The collection name.
            table: The underlying database table name.
        """
        self.model_name = model_name
        self.id = id
        self.collection = collection
        self.table = table
        super().__init__(
            f"{model_name} with id={id} has already been written "
            f"to collection '{collection}' (table '{table}'). "
            "Use update_object() to update the existing row, "
            "or pass duplicate=True to insert a new row."
        )


class ViewAlreadyExistsError(Exception):
    """Raised when create_view() is called for a view that already exists."""

    def __init__(self, view_name: str, collection: str):
        """Initialize ViewAlreadyExistsError.

        Args:
            view_name: The full view name that already exists.
            collection: The collection name.
        """
        self.view_name = view_name
        self.collection = collection
        super().__init__(
            f"View '{view_name}' already exists in collection '{collection}'. "
            "Use overwrite=True to replace it."
        )


class ThreadSafetyError(Exception):
    """Raised when an async collection is created with a thread-unsafe Engine.

    ``AsyncCollection`` and ``AsyncTimeSeriesCollection`` use
    ``asyncio.to_thread`` to run SQLite operations in a background
    thread.  By default, Python's ``sqlite3`` module forbids using a
    connection from a thread other than the one that created it.

    To fix this, create the ``Engine`` with ``check_same_thread=False``::

        conn = Engine("my.db", check_same_thread=False)
        col  = AsyncCollection(conn, "library")  # OK
    """

    def __init__(self):
        super().__init__(
            "Async collections require Engine(check_same_thread=False). "
            "SQLite connections cannot be shared across threads by default."
        )


class UnlinkedParentError(Exception):
    """Raised when writing an object with unset parent_ref fields.

    Every ``parent_ref`` field must be linked to a persisted parent
    before the child object can be written. Use
    ``collection.write_child()`` or pass the parent object directly
    at construction time to set the reference.
    """

    def __init__(self, model_name: str, field: str, parent_model_name: str):
        """Initialize UnlinkedParentError.

        Args:
            model_name: Name of the child model class.
            field: The parent_ref field that is None.
            parent_model_name: Name of the expected parent model class.
        """
        self.model_name = model_name
        self.field = field
        self.parent_model_name = parent_model_name
        super().__init__(
            f"{model_name}.{field} is a parent_ref({parent_model_name}) "
            f"and cannot be None at write time. "
            f"Use collection.write_child(obj, parent=parent) or "
            f"pass the parent at construction: Child(field=parent)."
        )


class ObjectNotPersistedError(Exception):
    """Raised when update_object() is called on an object not yet in the database."""

    def __init__(self, model_name: str, collection: str, table: str):
        """Initialize ObjectNotPersistedError.

        Args:
            model_name: Name of the model class.
            collection: The collection name.
            table: The underlying database table name.
        """
        self.model_name = model_name
        self.collection = collection
        self.table = table
        super().__init__(
            f"{model_name} has never been written to the database. "
            f"Cannot update in collection '{collection}' (table '{table}'). "
            "Use write_object() to insert it first."
        )


class ParentHasChildrenError(Exception):
    """Raised when deleting a parent row while ``on_delete='restrict'`` children exist.

    The default ``on_delete='restrict'`` mode on ``parent_ref`` refuses
    the delete instead of leaving orphan rows. Delete the children
    explicitly first (e.g. via
    ``collection.delete_objects(Child.fields.<parent_field> ==
    parent.row.id)``) and then re-issue the parent delete.
    """

    def __init__(
        self,
        parent_class: str,
        child_class: str,
        field: str,
        collection: str,
    ):
        """Initialize ParentHasChildrenError.

        Args:
            parent_class: Name of the parent model class.
            child_class: Name of the child model class that still has rows.
            field: The ``parent_ref`` field name on the child class.
            collection: The collection name.
        """
        self.parent_class = parent_class
        self.child_class = child_class
        self.field = field
        self.collection = collection
        super().__init__(
            f"Cannot delete {parent_class} from collection '{collection}': "
            f"{child_class} rows still reference it via "
            f"parent_ref field '{field}' "
            f"(on_delete='restrict', the default). "
            f"Delete the children first, e.g. "
            f"collection.delete_objects({child_class}.fields.{field} == parent.row.id)."
        )
