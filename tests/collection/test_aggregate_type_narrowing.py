"""Type-narrowing helpers for max_field / min_field.

`_unwrap_aggregate_annotation` and `_normalize_aggregate_type` decide
which model fields are eligible for max/min aggregation. They are
validation-critical: a wrong "yes" silently emits invalid SQL, a
wrong "no" blocks a legitimate query. Pin every branch explicitly.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Optional

from centaurodb.collection import (
    _normalize_aggregate_type,
    _unwrap_aggregate_annotation,
)


class TestUnwrapAnnotation:
    def test_plain_type_passes_through(self):
        assert _unwrap_aggregate_annotation(int) is int

    def test_annotated_unwraps_to_inner_type(self):
        assert _unwrap_aggregate_annotation(Annotated[int, "tag"]) is int

    def test_optional_unwraps_to_inner_type(self):
        assert _unwrap_aggregate_annotation(Optional[int]) is int

    def test_union_with_two_non_none_arms_rejected(self):
        # int | str has two non-None arms — ambiguous, must reject.
        assert _unwrap_aggregate_annotation(int | str) is None

    def test_parametrised_generic_rejected(self):
        # list[int] is not a `type` after unwrap → None.
        assert _unwrap_aggregate_annotation(list[int]) is None


class TestNormalizeAggregateType:
    def test_int_normalised_to_int(self):
        assert _normalize_aggregate_type(int) is int

    def test_float_normalised_to_float(self):
        assert _normalize_aggregate_type(float) is float

    def test_date_normalised_to_date(self):
        assert _normalize_aggregate_type(date) is date

    def test_datetime_normalised_to_datetime(self):
        assert _normalize_aggregate_type(datetime) is datetime

    def test_bool_rejected(self):
        # bool is a subclass of int, but max(bool) is meaningless.
        assert _normalize_aggregate_type(bool) is None

    def test_unsupported_type_returns_none(self):
        # str hits the trailing `return None` fallback.
        assert _normalize_aggregate_type(str) is None

    def test_unwrap_failure_propagates_none(self):
        # Ambiguous union → unwrap returns None → normalize short-circuits.
        assert _normalize_aggregate_type(int | str) is None
