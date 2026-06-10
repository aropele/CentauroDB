import pytest
from centaurodb.model import CentauroModel
from centaurodb.query import Field, OrGroup


class User(CentauroModel):
    __centauro_name__ = "User"
    name: str = "Unknown"
    age: int = 0
    role: str = "user"


def test_model_fields_resolves_fields():
    assert User.fields.age.name == "age"
    assert User.fields.role.name == "role"
    assert User.fields.name.name == "name"


def test_model_fields_resolves_standard_columns():
    assert User.fields.id.name == "id"
    assert User.fields.write_time.name == "write_time"
    assert User.fields.edit_time.name == "edit_time"


def test_model_fields_rejects_invalid_fields():
    with pytest.raises(AttributeError, match="Field 'invalid_field' not found"):
        User.fields.invalid_field


def test_field_operators_produce_conditions():
    f = Field("age")
    assert (f == 5).op == "eq"
    assert (f != 5).op == "neq"
    assert (f > 5).op == "gt"
    assert (f >= 5).op == "gte"
    assert (f < 5).op == "lt"
    assert (f <= 5).op == "lte"
    assert f.like("a%").op == "like"
    assert f.is_in([1, 2]).op == "in"


def test_field_conditions_carry_field_name_and_value():
    c = Field("role") == "admin"
    assert c.field == "role"
    assert c.value == "admin"


def test_model_fields_operators_match_field_operators():
    c = User.fields.age > 30
    assert c.field == "age"
    assert c.op == "gt"
    assert c.value == 30


def test_model_fields_condition_carries_model_class():
    c = User.fields.age > 30
    assert c.model_class is User


def test_raw_field_condition_has_no_model_class():
    c = Field("age") > 30
    assert c.model_class is None


# --- OrGroup construction ---


def test_condition_or_condition_returns_or_group():
    c1 = User.fields.role == "admin"
    c2 = User.fields.role == "editor"
    result = c1 | c2
    assert isinstance(result, OrGroup)
    assert len(result.conditions) == 2
    assert result.conditions[0] is c1
    assert result.conditions[1] is c2


def test_or_group_or_condition_accumulates():
    c1 = User.fields.role == "admin"
    c2 = User.fields.role == "editor"
    c3 = User.fields.role == "viewer"
    result = c1 | c2 | c3
    assert isinstance(result, OrGroup)
    assert len(result.conditions) == 3


def test_or_group_carries_model_class():
    c1 = User.fields.role == "admin"
    c2 = User.fields.role == "editor"
    result = c1 | c2
    assert result.model_class is User


def test_or_group_model_class_from_first_condition():
    c1 = User.fields.role == "admin"
    c2 = Field("role") == "editor"  # no model_class
    result = c1 | c2
    assert result.model_class is User
