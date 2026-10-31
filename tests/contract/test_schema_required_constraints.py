"""Execute the extracted `required` schema constraints against the packaged OWS 1.0.3 schema.

Scope: each credited row's enclosing subschema is checked with Python's jsonschema
Draft 2020-12 validator. A generated instance must be valid, and removing each
listed property must make it invalid. This does not exercise runtime admission, the
JavaScript validator or any proposed multi-runtime test, and does not verify the row.
"""

import pytest
from schema_constraint_support import BRIDGE, enclosing, gen, valid

SELECTOR = (
    "tests/contract/test_schema_required_constraints.py::test_required_property_removal_is_rejected"
)


def _enclosing(row):
    return enclosing(row, "required")


def _evaluate(row):
    """Return (instance, None) when the row is credited, else (None, reason)."""
    sub = _enclosing(row)
    instance = gen(sub)
    if not isinstance(instance, dict) or not valid(sub, instance):
        return None, "no valid instance could be generated for the enclosing subschema"
    for name in row["value"]:
        if valid(sub, {key: item for key, item in instance.items() if key != name}):
            return None, f"{name!r} can be removed without rejection in isolation"
    return instance, None


ROWS = [row for row in BRIDGE["schema_constraint_inventory"] if row["keyword"] == "required"]
EVALUATED = {row["id"]: _evaluate(row) for row in ROWS}
CREDITED = [row["id"] for row in ROWS if EVALUATED[row["id"]][1] is None]


@pytest.mark.parametrize("row_id", CREDITED)
def test_required_property_removal_is_rejected(row_id):
    row = next(item for item in ROWS if item["id"] == row_id)
    instance, reason = EVALUATED[row_id]
    assert reason is None and instance is not None
    sub = _enclosing(row)
    assert valid(sub, instance)
    for name in row["value"]:
        assert name in instance
        assert not valid(sub, {key: item for key, item in instance.items() if key != name}), name


def test_required_credit_set_matches_the_coverage_bridge():
    credited = {
        row["id"]: row
        for row in BRIDGE["schema_constraint_inventory"]
        if SELECTOR in row["runner_selectors"]
    }
    assert set(credited) == set(CREDITED)
    assert len(CREDITED) >= 67
    for row in BRIDGE["schema_constraint_inventory"]:
        if row["id"] in credited:
            assert row["keyword"] == "required" and row["status"] == "executed"
        elif row["keyword"] == "required":
            assert row["status"] == "unmapped"
