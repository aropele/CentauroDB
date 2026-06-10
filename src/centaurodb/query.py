from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Literal, TypeVar
from weakref import WeakKeyDictionary

if TYPE_CHECKING:
    from centaurodb.model import CentauroModel

Operator = Literal["eq", "neq", "gt", "gte", "lt", "lte", "like", "in"]


@dataclass
class Condition:
    """A single filter condition for querying objects.

    Normally created via ``Field`` operators rather than
    directly.

    Attributes:
        field: The name of the field to filter on.
        op: The comparison operator.
        value: The value to compare against.
        model_class: The model class this condition belongs to, set
            automatically when using ``Model.fields.field_name`` syntax.
    """

    field: str
    op: Operator
    value: Any
    model_class: type | None = None

    def __or__(self, other: Condition | OrGroup) -> OrGroup:
        if isinstance(other, OrGroup):
            return OrGroup(
                [self] + other.conditions, self.model_class or other.model_class
            )
        return OrGroup([self, other], self.model_class or other.model_class)


@dataclass
class OrGroup:
    """A group of conditions joined by OR.

    Created via the ``|`` operator between ``Condition`` objects.

    Attributes:
        conditions: The conditions to OR together.
        model_class: The model class shared by all conditions.
    """

    conditions: list[Condition]
    model_class: type | None = None

    def __or__(self, other: Condition | OrGroup) -> OrGroup:
        extra = other.conditions if isinstance(other, OrGroup) else [other]
        model = self.model_class or (
            other.model_class if isinstance(other, OrGroup) else other.model_class
        )
        return OrGroup(self.conditions + extra, model)


class Field:
    """A named field that produces ``Condition`` objects via operators.

    Use Python comparison operators or the ``like`` / ``is_in`` methods
    to build conditions for ``read_objects_by_query``.

    Args:
        name: The field name.
        model_class: The model class this field belongs to. Set automatically
            when accessed via ``Model.fields.field_name``.

    Examples:
        >>> f = Field("level")
        >>> f > 5
        Condition(field='level', op='gt', value=5, model_class=None)
        >>> f.is_in([1, 2, 3])
        Condition(field='level', op='in', value=[1, 2, 3], model_class=None)
    """

    def __init__(self, name: str, model_class: type | None = None):
        self.name = name
        self.model_class = model_class

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, value: Any) -> Condition:  # type: ignore[override]
        """Build an equality condition.

        Examples:
            >>> Field("status") == "active"
            Condition(field='status', op='eq', value='active', model_class=None)
        """
        return Condition(self.name, "eq", value, self.model_class)

    def __ne__(self, value: Any) -> Condition:  # type: ignore[override]
        """Build a not-equal condition.

        Examples:
            >>> Field("status") != "active"
            Condition(field='status', op='neq', value='active', model_class=None)
        """
        return Condition(self.name, "neq", value, self.model_class)

    def __gt__(self, value: Any) -> Condition:
        """Build a greater-than condition.

        Examples:
            >>> Field("level") > 5
            Condition(field='level', op='gt', value=5, model_class=None)
        """
        return Condition(self.name, "gt", value, self.model_class)

    def __ge__(self, value: Any) -> Condition:
        """Build a greater-than-or-equal condition.

        Examples:
            >>> Field("level") >= 5
            Condition(field='level', op='gte', value=5, model_class=None)
        """
        return Condition(self.name, "gte", value, self.model_class)

    def __lt__(self, value: Any) -> Condition:
        """Build a less-than condition.

        Examples:
            >>> Field("level") < 5
            Condition(field='level', op='lt', value=5, model_class=None)
        """
        return Condition(self.name, "lt", value, self.model_class)

    def __le__(self, value: Any) -> Condition:
        """Build a less-than-or-equal condition.

        Examples:
            >>> Field("level") <= 5
            Condition(field='level', op='lte', value=5, model_class=None)
        """
        return Condition(self.name, "lte", value, self.model_class)

    def like(self, value: str) -> Condition:
        """Build a SQL LIKE pattern-matching condition.

        Args:
            value: A SQL LIKE pattern (``%`` for wildcard).

        Returns:
            A Condition with the ``like`` operator.

        Examples:
            >>> Field("location").like("gar%")
            Condition(field='location', op='like', value='gar%', model_class=None)
        """
        return Condition(self.name, "like", value, self.model_class)

    _IS_IN_MAX_SIZE = 5000

    def is_in(self, values: list[Any]) -> Condition:
        """Build a SQL IN membership condition.

        Args:
            values: A non-empty list of values to match against.
                Maximum length is 5000 to prevent generating
                excessively large SQL strings.

        Returns:
            A Condition with the ``in`` operator.

        Raises:
            ValueError: If the values list is empty or exceeds
                5000 elements.

        Examples:
            >>> Field("status").is_in(["active", "pending"])
            Condition(field='status', op='in', value=['active', 'pending'], model_class=None)
        """
        if not values:
            raise ValueError(
                "is_in() requires a non-empty list. "
                "An empty list would produce invalid SQL."
            )
        if len(values) > self._IS_IN_MAX_SIZE:
            raise ValueError(
                f"is_in() received {len(values)} values, exceeding the "
                f"{self._IS_IN_MAX_SIZE} element limit. For large datasets, "
                f"consider loading your filter values into a view with "
                f"create_view() and using a raw SQL join via sql_select()."
            )
        return Condition(self.name, "in", values, self.model_class)


TModel = TypeVar("TModel", bound="CentauroModel")


class FieldProxy(Generic[TModel]):
    """Attribute access helper backing ``Model.fields``.

    Returns ``Field`` objects for valid columns and raises ``AttributeError``
    for unknown names to keep queries type-safe.
    """

    _allowed_fields = {"id", "write_time", "edit_time"}

    def __init__(self, model: type[TModel]):
        self._model = model

    def __getattr__(self, name: str) -> Field:
        if name in self._allowed_fields:
            return Field(name, self._model)

        if name in self._model.model_fields:
            return Field(name, self._model)

        raise AttributeError(
            f"Field '{name}' not found in model '{self._model.__name__}' "
            f"or standard columns."
        )


class FieldAccessor(Generic[TModel]):
    """Descriptor that exposes a ``FieldProxy`` via ``Model.fields``."""

    _cache: ClassVar[WeakKeyDictionary[type["CentauroModel"], FieldProxy[Any]]] = (
        WeakKeyDictionary()
    )

    def __get__(self, obj: TModel | None, owner: type[TModel]) -> FieldProxy[TModel]:
        proxy = self._cache.get(owner)
        if proxy is None:
            proxy = FieldProxy(owner)
            self._cache[owner] = proxy
        return proxy
