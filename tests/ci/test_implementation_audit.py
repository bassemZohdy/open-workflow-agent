"""Check audit integrity and prevent source inventories becoming support claims.

These checks verify evidence links and ownership. Behavioral qualification is
provided by the referenced fixtures and failure probes, never these checks.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(name: str):
    return json.loads((ROOT / "resources/compatibility" / name).read_text(encoding="utf-8"))


@lru_cache
def _symbols(path: str) -> set[str]:
    names: set[str] = set()

    def walk(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                names.add(name)
                walk(child, name + ".")
            else:
                walk(child, prefix)

    walk(ast.parse((ROOT / path).read_text(encoding="utf-8")))
    return names


def test_every_inventory_feature_has_independent_current_audit_and_owner():
    tracker = _read("feature-tracker.json")
    audit = _read("implementation-audit.json")
    inventory = {row["id"]: row for row in tracker["matrix_a"] + tracker["matrix_b"]}
    rows = audit["features"]
    assert len(rows) == len(inventory) == 658
    assert {row["feature_id"] for row in rows} == set(inventory)
    task_ids = {task["id"] for task in tracker["repository_tracking"]["tasks"]}
    tracking = {row["feature_id"]: row for row in tracker["staged_delivery"]["tracking"]}
    for row in rows:
        original = inventory[row["feature_id"]]
        assert row["feature"] == original["feature"]
        assert row["category"] == original["category"]
        assert row["construct"] == original.get("ows_construct", original.get("ows_equivalent"))
        assert row["proposed_tests"] == original["required_test"]
        assert row["review"] and row["javascript_gap"]
        assert set(tracking[row["feature_id"]]["todo_ids"]) <= set(row["todo_ids"])
        assert set(row["todo_ids"]) <= task_ids
        assert {"S4-03", "S4-04"} <= set(row["todo_ids"])
        group = audit["evidence_groups"][row["evidence_group"]]
        assert row["python_outcome"] == group["outcome"]
        assert set(group["remediation_tasks"]) <= set(row["todo_ids"])
    assert audit["outcome_counts"] == dict(Counter(row["python_outcome"] for row in rows))


def test_audit_anchors_point_to_active_code_and_actual_test_functions():
    audit = _read("implementation-audit.json")
    used = {row["evidence_group"] for row in audit["features"]}
    assert used == set(audit["evidence_groups"])
    for group in audit["evidence_groups"].values():
        assert group["outcome"] in audit["outcome_definitions"]
        assert group["observed"] and group["remaining_scope"] and group["test_scope"]
        assert group["code"]
        if group["outcome"] in {"qualified_subset", "partial", "rejected", "internal_only"}:
            assert group["tests"], group
        for ref in group["code"] + group["tests"]:
            path = Path(ref["path"])
            assert not path.is_absolute() and ".." not in path.parts
            assert "migration/reference" not in ref["path"]
            assert (ROOT / path).is_file()
            assert ref["symbol"] in _symbols(ref["path"]), ref
        for ref in group["tests"]:
            assert ref["path"].startswith("tests/") and ref["symbol"].startswith("test_")
        for ref in group["code"]:
            assert ref["path"].startswith(("core/", "engines/"))
            assert ref["path"] in audit["reviewed_file_sha256"]
    assert re.fullmatch(r"[a-f0-9]{40}", audit["reviewed_implementation_commit"])
    assert all(
        re.fullmatch(r"[a-f0-9]{64}", value) for value in audit["reviewed_file_sha256"].values()
    )


def test_audit_preserves_unexecuted_full_criteria_and_specific_hidden_gaps():
    tracker = _read("feature-tracker.json")
    audit = _read("implementation-audit.json")
    assert audit["inventory_counts"] == {
        "features": 658,
        "proposed_tests": 443,
        "schema_constraints": 774,
    }
    assert not audit["full_conformance"]
    assert len(tracker["tests"]) == 443
    assert len(tracker["schema_constraints"]) == 774
    assert all(test["status"] == "Proposed; not executed" for test in tracker["tests"])
    assert all(not row["full_feature_verified"] for row in audit["features"])
    assert all(not row["full_proposed_criteria_executed"] for row in audit["features"])
    assert all(row["javascript_outcome"] == "unimplemented" for row in audit["features"])
    rows = {row["feature_id"]: row for row in audit["features"]}
    assert rows["A058"]["python_outcome"] == "qualified_subset"
    assert "S2-01" in rows["A058"]["todo_ids"]
    assert rows["A057"]["python_outcome"] == "unimplemented"  # SDK != public reducers.
    assert rows["AS082"]["evidence_group"] == "http_raw"
    assert rows["AS138"]["evidence_group"] == "mcp_inert"
    assert rows["AS180"]["evidence_group"] == "container_inert"
    assert rows["AS305"]["evidence_group"] == "extensions"
    assert rows["A161"]["evidence_group"] == "a2a_finite"  # Finite Python A2A qualification is separately bounded.
    assert rows["A159"]["evidence_group"] == "a2a_finite"  # Bounded stream behavior is covered by the finite A2A profile.
    assert rows["B043"]["python_outcome"] == "unimplemented"  # Saver != public history.
    assert rows["B078"]["python_outcome"] == "partial"  # Bounded scope restart only.
    assert audit["gap_probes"]["unexpected_passes"] == 0


def test_tracker_links_to_audit_without_promoting_full_feature_completion():
    tracker = _read("feature-tracker.json")
    audit = _read("implementation-audit.json")
    audited = {row["feature_id"]: row for row in audit["features"]}
    for row in tracker["staged_delivery"]["tracking"]:
        link = row["current_implementation_audit"]
        assert link["path"] == "resources/compatibility/implementation-audit.json"
        assert link["feature_id"] == row["feature_id"]
        assert link["python_outcome"] == audited[row["feature_id"]]["python_outcome"]
        assert set(audited[row["feature_id"]]["todo_ids"]) <= set(row["todo_ids"])
        assert row["delivery_status"] == "Tracked"
        assert (
            row["javascript_delivery_status"] == "Unimplemented; independent qualification pending"
        )
