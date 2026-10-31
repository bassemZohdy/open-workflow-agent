"""Execute closed-object, value-domain and bound schema constraints in isolation.

A row is credited only when some probe value is accepted by a copy of the enclosing
subschema with that single keyword removed and rejected by the real subschema, so the
keyword itself is shown to be the cause. Python's jsonschema Draft 2020-12 validator
against the packaged OWS 1.0.3 schema only: not runtime admission, not JavaScript, not
any multi-runtime proposed test, and no row is verified.
"""

import pytest
from schema_constraint_support import BRIDGE, enclosing, gen, valid, without

SELECTOR = (
    "tests/contract/test_schema_value_constraints.py::"
    "test_keyword_is_the_isolated_cause_of_rejection"
)
KEYWORDS = {
    "unevaluatedProperties",
    "additionalProperties",
    "enum",
    "const",
    "minimum",
    "maximum",
    "minLength",
    "minItems",
    "minProperties",
    "maxProperties",
    "type",
}
WRONG_TYPES = {
    "string": ["x"],
    "integer": [1],
    "number": [1.5, 1],
    "boolean": [True],
    "null": [None],
    "array": [[]],
    "object": [{}],
}
SENTINELS = ["__not-in-enum__", 0, True, None, [], {}]


def _beyond(row, sub):
    """Candidate values that violate only the row's keyword, plus values it must accept."""
    keyword, value = row["keyword"], row["value"]
    if keyword in {"unevaluatedProperties", "additionalProperties"}:
        base = gen(sub)
        if value is not False or not isinstance(base, dict):
            return [], []
        return [{**base, "__unknown__": 1}], [base]
    if keyword == "type":
        declared = [value] if isinstance(value, str) else list(value)
        probes = [
            probe
            for kind, items in WRONG_TYPES.items()
            if kind not in declared and not (kind == "number" and "integer" in declared)
            for probe in items
            if not (isinstance(probe, int) and not isinstance(probe, bool) and "number" in declared)
        ]
        return probes, []
    if keyword == "enum":
        return [item for item in SENTINELS if item not in value], list(value)
    if keyword == "const":
        return [item for item in SENTINELS if item != value], [value]
    if keyword == "minimum":
        return [value - 1], [value]
    if keyword == "maximum":
        return [value + 1], [value]
    if keyword == "minLength":
        return ["x" * (value - 1)], ["x" * value]
    if keyword == "minItems":
        return [[0] * (value - 1)], [[0] * value]
    if keyword == "minProperties":
        return [{f"p{i}": 1 for i in range(value - 1)}], []
    if keyword == "maxProperties":
        return [{f"p{i}": 1 for i in range(value + 1)}], []
    return [], []


def _probe(row):
    """Return (rejected_candidate, accepted_candidates, reason); candidate is None if uncredited."""
    try:
        sub = enclosing(row, row["keyword"])
    except (KeyError, IndexError, ValueError):
        return None, [], "the extracted pointer does not resolve in the packaged schema tree"
    if row["keyword"] == "type":
        declared = [row["value"]] if isinstance(row["value"], str) else row["value"]
        if not isinstance(declared, list) or any(item not in WRONG_TYPES for item in declared):
            return None, [], "the row names a property called `type`, not a type keyword"
    control = without(sub, row["keyword"])
    try:
        beyond, within = _beyond(row, sub)
        accepted = [item for item in within if valid(sub, item)]
        if len(accepted) != len(within):
            return None, [], "an in-bounds value is not accepted by the enclosing subschema"
        for candidate in beyond:
            if valid(control, candidate) and not valid(sub, candidate):
                return candidate, accepted, ""
    except Exception as error:  # an unusable extracted subschema is an exclusion, not a failure
        return None, [], f"the enclosing subschema is not a usable schema ({type(error).__name__})"
    return None, [], "no probe value isolates this keyword from the rest of its subschema"


ROWS = [row for row in BRIDGE["schema_constraint_inventory"] if row["keyword"] in KEYWORDS]
PROBES = {row["id"]: _probe(row) for row in ROWS}
CREDITED = [row["id"] for row in ROWS if PROBES[row["id"]][0] is not None]


@pytest.mark.parametrize("row_id", CREDITED)
def test_keyword_is_the_isolated_cause_of_rejection(row_id):
    row = next(item for item in ROWS if item["id"] == row_id)
    sub = enclosing(row, row["keyword"])
    control = without(sub, row["keyword"])
    rejected, accepted, _ = PROBES[row_id]
    assert valid(control, rejected)
    assert not valid(sub, rejected)
    assert all(valid(sub, item) for item in accepted)


def test_value_credit_set_matches_the_coverage_bridge():
    credited = {
        row["id"]: row
        for row in BRIDGE["schema_constraint_inventory"]
        if SELECTOR in row["runner_selectors"]
    }
    assert set(credited) == set(CREDITED)
    assert len(CREDITED) >= 1
    for row in BRIDGE["schema_constraint_inventory"]:
        if row["id"] in credited:
            assert row["keyword"] in KEYWORDS and row["status"] == "executed"
        elif row["keyword"] in KEYWORDS:
            assert row["status"] == "unmapped"
