"""Execute the extracted `required` schema constraints against the packaged OWS 1.0.3 schema.

Scope: each credited row's enclosing subschema is checked with Python's jsonschema
Draft 2020-12 validator. A generated instance must be valid, and removing each
listed property must make it invalid. This does not exercise runtime admission, the
JavaScript validator or any proposed multi-runtime test, and does not verify the row.
"""

import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from open_workflow_agent.workflow import _JsonBooleanLoader

REPOSITORY = Path(__file__).resolve().parents[2]
ROOT = yaml.load(
    (
        REPOSITORY / "core/src/open_workflow_agent/_resources/open-workflow/1.0.3/workflow.yaml"
    ).read_text(encoding="utf-8"),
    Loader=_JsonBooleanLoader,
)
BRIDGE = json.loads(
    (REPOSITORY / "resources/compatibility/coverage-bridge.json").read_text(encoding="utf-8")
)
SELECTOR = (
    "tests/contract/test_schema_required_constraints.py::test_required_property_removal_is_rejected"
)


def unescape(part):
    return part.replace("~1", "/").replace("~0", "~")


def resolve(pointer, root=ROOT):
    node = root
    for part in [p for p in pointer.split("/") if p != ""]:
        node = node[int(part)] if isinstance(node, list) else node[unescape(part)]
    return node


def wrapper(sub):
    if sub is ROOT:
        return ROOT
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": ROOT.get("$defs", {}),
    }
    schema.update(sub)
    return schema


def valid(sub, inst):
    return Draft202012Validator(wrapper(sub)).is_valid(inst)


STR = ["x", "https://example.com/", "P1D", "1.0.0", "application/json", "a-b", "${ . }", "1"]
NUM = [1, 0, 200, 100, 1.5]


def merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(out[k], v) if k in out else v
        return out
    return b


def gen(sub, depth=0):
    if depth > 12:
        return None
    if "$ref" in sub and sub["$ref"].startswith("#/"):
        return gen(resolve(sub["$ref"][1:]), depth + 1)
    if "const" in sub:
        return sub["const"]
    if "enum" in sub:
        return sub["enum"][0]
    result = None
    for key in ("oneOf", "anyOf"):
        if key in sub:
            for branch in sub[key]:
                cand = gen(branch, depth + 1)
                if cand is not None and valid(branch, cand):
                    result = cand
                    break
            if result is None:
                return None
            break
    for part in sub.get("allOf", []):
        cand = gen(part, depth + 1)
        if cand is None:
            return None
        result = cand if result is None else merge(result, cand)
    t = sub.get("type")
    if t == "object" or "properties" in sub or "required" in sub:
        obj = result if isinstance(result, dict) else {}
        props = sub.get("properties", {})
        for name in sub.get("required", []):
            if name not in obj:
                v = gen(props.get(name, {}), depth + 1)
                if v is None and name in props:
                    return None
                obj[name] = {} if v is None else v
        need = sub.get("minProperties", 0)
        for name, ps in props.items():
            if len(obj) >= need:
                break
            if name not in obj:
                v = gen(ps, depth + 1)
                if v is not None:
                    obj[name] = v
        return obj
    if result is not None:
        return result
    if t == "string":
        for c in STR:
            if valid(sub, c):
                return c
        return None
    if t in ("integer", "number"):
        for c in NUM:
            if valid(sub, c):
                return c
        return None
    if t == "boolean":
        return True
    if t == "null":
        return None
    if t == "array":
        n = sub.get("minItems", 0)
        item = gen(sub.get("items", {}), depth + 1) if n else None
        return [copy.deepcopy(item) for _ in range(n)]
    return {} if not t else None


def _enclosing(row):
    pointer = row["pointer"]
    return ROOT if pointer == "/required" else resolve(pointer.rsplit("/required", 1)[0])


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
