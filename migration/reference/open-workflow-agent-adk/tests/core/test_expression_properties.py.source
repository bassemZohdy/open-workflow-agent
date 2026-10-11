from hypothesis import given
from hypothesis import strategies as st

from openworkflow_adk.expressions import bind, evaluate


@given(st.integers(min_value=-(2**53) + 1, max_value=2**53 - 1))
def test_integer_expression_round_trip(value: int) -> None:
    assert evaluate(str(value)) == value


@given(st.dictionaries(st.text(min_size=1, max_size=8), st.integers(), max_size=5))
def test_bind_preserves_non_expression_values(values: dict[str, int]) -> None:
    assert bind(values) == values
