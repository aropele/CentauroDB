import pytest
from pydantic import ConfigDict, ValidationError

from centaurodb import CentauroModel, renamed_from


class StrictSensor(CentauroModel):
    __centauro_name__ = "StrictSensor"
    value: float = 0.0


class RelaxedSensor(CentauroModel):
    __centauro_name__ = "RelaxedSensor"
    model_config = ConfigDict(validate_assignment=False)
    value: float = 0.0


class TestValidateAssignment:
    def test_validates_on_assignment_by_default(self):
        obj = StrictSensor(value=1.0)
        with pytest.raises(ValidationError):
            obj.value = "not a float"  # type: ignore

    def test_opt_out_skips_validation(self):
        obj = RelaxedSensor(value=1.0)
        obj.value = "not a float"  # type: ignore
        assert obj.value == "not a float"


class TestCentauroNameRequired:
    def test_missing_centauro_name_raises(self):
        with pytest.raises(TypeError, match="must define a __centauro_name__"):

            class Bad(CentauroModel):
                value: float = 0.0

    def test_centauro_name_set_ok(self):
        class Good(CentauroModel):
            __centauro_name__ = "Good"
            value: float = 0.0

        assert Good.__centauro_name__ == "Good"

    def test_empty_string_raises(self):
        with pytest.raises(TypeError, match="must define a __centauro_name__"):

            class Bad(CentauroModel):
                __centauro_name__ = ""
                value: float = 0.0


class TestCentauroNameWhitelist:
    """Stricter validation: only identifier-shaped names are allowed.

    The persistent name is interpolated into SQL (CREATE VIEW WHERE
    name IN (...) and similar), so anything outside ``[A-Za-z0-9_]``
    starting with a letter or underscore is a potential injection
    vector and must be rejected at class-definition time.
    """

    def test_semicolon_rejected(self):
        with pytest.raises(TypeError, match="invalid __centauro_name__"):

            class Bad(CentauroModel):
                __centauro_name__ = "drop;table"
                value: float = 0.0

    def test_double_dash_rejected(self):
        with pytest.raises(TypeError, match="invalid __centauro_name__"):

            class Bad(CentauroModel):
                __centauro_name__ = "name--comment"
                value: float = 0.0

    def test_space_rejected(self):
        with pytest.raises(TypeError, match="invalid __centauro_name__"):

            class Bad(CentauroModel):
                __centauro_name__ = "has space"
                value: float = 0.0

    def test_double_quote_rejected(self):
        with pytest.raises(TypeError, match="invalid __centauro_name__"):

            class Bad(CentauroModel):
                __centauro_name__ = 'has"double'
                value: float = 0.0

    def test_single_quote_rejected(self):
        with pytest.raises(TypeError, match="invalid __centauro_name__"):

            class Bad(CentauroModel):
                __centauro_name__ = "it's_bad"
                value: float = 0.0

    def test_leading_digit_rejected(self):
        with pytest.raises(TypeError, match="invalid __centauro_name__"):

            class Bad(CentauroModel):
                __centauro_name__ = "1book"
                value: float = 0.0

    def test_identifier_accepted(self):
        class GoodA(CentauroModel):
            __centauro_name__ = "Book"
            value: float = 0.0

        class GoodB(CentauroModel):
            __centauro_name__ = "_internal_table_2"
            value: float = 0.0

        assert GoodA.__centauro_name__ == "Book"
        assert GoodB.__centauro_name__ == "_internal_table_2"


class TestRequiredFieldsRejected:
    def test_required_field_raises(self):
        with pytest.raises(TypeError, match="must have a default value"):

            class Bad(CentauroModel):
                __centauro_name__ = "Bad"
                name: str

    def test_optional_with_default_ok(self):
        class Good(CentauroModel):
            __centauro_name__ = "Good"
            name: str = ""

        assert Good().name == ""

    def test_optional_none_ok(self):
        class Good(CentauroModel):
            __centauro_name__ = "Good"
            name: str | None = None

        assert Good().name is None

    def test_mixed_required_and_default_raises(self):
        with pytest.raises(TypeError, match="must have a default value"):

            class Bad(CentauroModel):
                __centauro_name__ = "Bad"
                ok_field: int = 0
                bad_field: str


class TestExtraForbidRejected:
    def test_extra_forbid_raises(self):
        with pytest.raises(TypeError, match="cannot use extra='forbid'"):

            class Bad(CentauroModel):
                __centauro_name__ = "Bad"
                model_config = ConfigDict(extra="forbid")
                value: float = 0.0

    def test_extra_ignore_ok(self):
        class Good(CentauroModel):
            __centauro_name__ = "Good"
            model_config = ConfigDict(extra="ignore")
            value: float = 0.0

        assert Good(value=1.0).value == 1.0

    def test_extra_allow_ok(self):
        class Good(CentauroModel):
            __centauro_name__ = "Good"
            model_config = ConfigDict(extra="allow")
            value: float = 0.0

        assert Good(value=1.0).value == 1.0


class TestRenamedFrom:
    def test_reads_old_json_key(self):
        class Sensor(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", default=0.0)

        obj = Sensor.model_validate_json('{"temp": 42.0}')
        assert obj.temperature == 42.0

    def test_reads_new_json_key(self):
        class Sensor(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", default=0.0)

        obj = Sensor.model_validate_json('{"temperature": 99.0}')
        assert obj.temperature == 99.0

    def test_serializes_with_new_name(self):
        class Sensor(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", default=0.0)

        obj = Sensor.model_validate_json('{"temp": 42.0}')
        assert '"temperature"' in obj.model_dump_json()
        assert '"temp"' not in obj.model_dump_json()

    def test_uses_default_when_neither_key_present(self):
        class Sensor(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", default=0.0)

        obj = Sensor.model_validate_json("{}")
        assert obj.temperature == 0.0

    def test_multiple_old_names(self):
        class Sensor(CentauroModel):
            __centauro_name__ = "Sensor"
            temperature: float = renamed_from("temp", "t", default=0.0)

        obj1 = Sensor.model_validate_json('{"temp": 1.0}')
        obj2 = Sensor.model_validate_json('{"t": 2.0}')
        assert obj1.temperature == 1.0
        assert obj2.temperature == 2.0
