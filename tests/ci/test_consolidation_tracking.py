"""Prevent losing source files or compatibility gaps during consolidation."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_full_inventory_is_preserved_and_owned_by_tracked_tasks():
    tracker = json.loads((ROOT / "resources/compatibility/feature-tracker.json").read_text())
    rows = tracker["matrix_a"] + tracker["matrix_b"]
    assert len(tracker["matrix_a"]) == 575
    assert len(tracker["matrix_b"]) == 83
    assert len(tracker["tests"]) == 443
    assert len(tracker["schema_constraints"]) == 774
    assert len(tracker["extensions"]) == 83
    assert len(tracker["configuration"]) == 52
    ids = {row["id"] for row in rows}
    tracking = tracker["staged_delivery"]["tracking"]
    assert len(tracking) == len(ids) == 658
    assert {row["feature_id"] for row in tracking} == ids
    tasks = {task["id"] for task in tracker["repository_tracking"]["tasks"]}
    task_states = {task["id"]: task["status"] for task in tracker["repository_tracking"]["tasks"]}
    pending = {
        task["id"]
        for task in tracker["repository_tracking"]["tasks"]
        if task["status"] == "Pending"
    }
    todo = (ROOT / "TODO.md").read_text()
    assert all(f"**{task}**" in todo for task in pending)
    assert "**MIG-01**" not in todo
    tests = {test["id"] for test in tracker["tests"]}
    for row in tracking:
        assert row["todo_ids"] and set(row["todo_ids"]) <= tasks
        if row["repository_status"] != "Verification pending":
            assert row["evidence_of_completion"]
            for evidence in row["evidence_of_completion"]:
                assert evidence["task_id"] in row["todo_ids"]
                assert evidence["task_id"] in task_states
                assert evidence["scope"]
                for path in evidence["tests"]:
                    assert (ROOT / path).is_file()
        else:
            assert row["evidence_of_completion"] == []
        assert set(re.findall(r"T\d+", row["required_tests"])) <= tests
    assert not tracker["repository_tracking"]["verification"]["full_conformance"]


def test_coverage_bridge_exhaustively_tracks_proposed_tests_and_schema_constraints():
    tracker = json.loads(
        (ROOT / "resources/compatibility/feature-tracker.json").read_text(encoding="utf-8")
    )
    bridge = json.loads(
        (ROOT / "resources/compatibility/coverage-bridge.json").read_text(encoding="utf-8")
    )
    assert bridge["format_version"] == 1
    assert len(bridge["test_inventory"]) == len(tracker["tests"]) == 443
    assert len(bridge["schema_constraint_inventory"]) == len(tracker["schema_constraints"]) == 774
    blockers = bridge["current_mapping_blockers"]
    for inventory_name, rows in (
        ("test_inventory", bridge["test_inventory"]),
        ("schema_constraint_inventory", bridge["schema_constraint_inventory"]),
    ):
        summary = blockers[inventory_name]
        assert summary["mapped_count"] == sum(row["status"] != "unmapped" for row in rows)
        assert summary["unmapped_count"] == sum(row["status"] == "unmapped" for row in rows)
        if summary["unmapped_count"]:
            assert len(summary["blocker"].strip()) >= 80

    tests = {item["id"]: item for item in tracker["tests"]}
    mapped_tests = {item["id"]: item for item in bridge["test_inventory"]}
    assert len(mapped_tests) == len(bridge["test_inventory"])
    assert set(mapped_tests) == set(tests)
    for item in mapped_tests.values():
        assert item["status"] in {"unmapped", "mapped_not_executed", "executed", "verified"}
        _assert_coverage_bridge_claim(item)

    constraints = tracker["schema_constraints"]
    mapped_constraints = bridge["schema_constraint_inventory"]
    assert [item["id"] for item in mapped_constraints] == [
        f"SC{index:04d}" for index in range(1, len(constraints) + 1)
    ]
    assert [
        (
            item["pointer"],
            item["keyword"],
            item["value"],
            item["reference"],
            item["verification"],
        )
        for item in mapped_constraints
    ] == [
        (
            item["pointer"],
            item["keyword"],
            item["value"],
            item["reference"],
            item["verification"],
        )
        for item in constraints
    ]
    for item in mapped_constraints:
        assert item["status"] in {"unmapped", "mapped_not_executed", "executed", "verified"}
        _assert_coverage_bridge_claim(item)


def _assert_coverage_bridge_claim(item):
    """Prevent an execution claim without a live pytest selector and retained evidence."""
    selectors = item["runner_selectors"]
    evidence = item["evidence_references"]
    if item["status"] == "unmapped":
        assert not selectors and not evidence
        assert item["reason"].strip()
        return
    if item["status"] == "mapped_not_executed":
        assert selectors and not evidence
    else:
        assert selectors and evidence

    for selector in selectors:
        path_text, separator, symbol = selector.partition("::")
        assert separator and symbol.startswith("test_")
        path = Path(path_text)
        assert not path.is_absolute() and ".." not in path.parts
        assert path_text.startswith("tests/") and path.suffix == ".py"
        assert (ROOT / path).is_file()
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        test_node = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol
            ),
            None,
        )
        assert test_node is not None
        assert not _has_nonexecuting_pytest_marker(tree, test_node)

    for reference in evidence:
        path_text, separator, anchor = reference.partition("#")
        assert separator and anchor
        path = Path(path_text)
        assert not path.is_absolute() and ".." not in path.parts
        assert path_text.startswith(("docs/", "tests/"))
        assert (ROOT / path).is_file()
        assert anchor in (ROOT / path).read_text(encoding="utf-8")


def _has_nonexecuting_pytest_marker(tree: ast.Module, test_node: ast.AST) -> bool:
    """Reject selectors that pytest will skip or treat as expected failures."""
    markers = ("skip", "skipif", "xfail")

    def matches_marker(node: ast.AST) -> bool:
        name = ast.unparse(node.func if isinstance(node, ast.Call) else node)
        return any(
            name == f"pytest.mark.{marker}" or name.startswith(f"pytest.mark.{marker}.")
            for marker in markers
        )

    decorators = getattr(test_node, "decorator_list", ())
    if any(matches_marker(decorator) for decorator in decorators):
        return True

    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
            value = statement.value
        else:
            continue
        if value is None or not any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets
        ):
            continue
        values = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
        if any(matches_marker(marker) for marker in values):
            return True
    return False


def test_coverage_bridge_rejects_skipped_or_expected_failure_selectors():
    tree = ast.parse(
        """
import pytest

@pytest.mark.skip(reason='not run')
def test_skipped():
    pass

@pytest.mark.xfail
async def test_expected_failure():
    pass

def test_active():
    pass
"""
    )
    selected = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert _has_nonexecuting_pytest_marker(tree, selected["test_skipped"])
    assert _has_nonexecuting_pytest_marker(tree, selected["test_expected_failure"])
    assert not _has_nonexecuting_pytest_marker(tree, selected["test_active"])

    module_skipped = ast.parse(
        """
import pytest
pytestmark = pytest.mark.skipif(True, reason='not run')
def test_module_skipped():
    pass
"""
    )
    function = next(
        node
        for node in ast.walk(module_skipped)
        if isinstance(node, ast.FunctionDef) and node.name == "test_module_skipped"
    )
    assert _has_nonexecuting_pytest_marker(module_skipped, function)


def test_apache_snapshot_exact_bytes_and_generic_source_boundary():
    manifest = json.loads((ROOT / "migration/source-manifest.json").read_text())
    sources = {source["repository"].split("/")[-1]: source for source in manifest["sources"]}
    adk = sources["open-workflow-agent-adk"]
    assert adk["license"] == "Apache-2.0"
    assert len(adk["files"]) == adk["tracked_file_count"] == 284
    specs = sources["specs-agent-platform"]
    assert specs["license"] == "Apache-2.0"
    assert len(specs["files"]) == specs["tracked_file_count"] == 109
    for source in (adk, specs):
        for item in source["files"]:
            data = (ROOT / item["destination"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == item["sha256"]
            blob = f"blob {len(data)}\0".encode() + data
            assert hashlib.sha1(blob).hexdigest() == item["git_blob_sha"]
    generic = sources["generic-agent-lang"]
    assert len(generic["files"]) == 102
    assert generic["license"] is None
    assert all("destination" not in item for item in generic["files"])
    assert manifest["private_source"]["visibility"] == "private"
    assert manifest["private_source"]["public_transfer"].startswith("Authorized by the user")
