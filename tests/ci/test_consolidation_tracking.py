"""Prevent losing source files or compatibility gaps during consolidation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_full_inventory_is_preserved_and_owned_by_pending_tasks():
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
    todo = (ROOT / "TODO.md").read_text()
    assert all(f"**{task}**" in todo for task in tasks)
    tests = {test["id"] for test in tracker["tests"]}
    for row in tracking:
        assert row["todo_ids"] and set(row["todo_ids"]) <= tasks
        assert row["repository_status"] == "Verification pending"
        assert row["evidence_of_completion"] == []
        assert set(re.findall(r"T\d+", row["required_tests"])) <= tests
    assert not tracker["repository_tracking"]["verification"]["full_conformance"]


def test_apache_snapshot_exact_bytes_and_generic_source_boundary():
    manifest = json.loads((ROOT / "migration/source-manifest.json").read_text())
    sources = {source["repository"].split("/")[-1]: source for source in manifest["sources"]}
    adk = sources["open-workflow-agent-adk"]
    assert adk["license"] == "Apache-2.0"
    assert len(adk["files"]) == adk["tracked_file_count"] == 284
    for item in adk["files"]:
        data = (ROOT / item["destination"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        blob = f"blob {len(data)}\0".encode() + data
        assert hashlib.sha1(blob).hexdigest() == item["git_blob_sha"]
    generic = sources["generic-agent-lang"]
    assert len(generic["files"]) == 102
    assert generic["license"] is None
    assert all("destination" not in item for item in generic["files"])
    assert manifest["private_source"]["visibility"] == "private"
    assert not (ROOT / "migration/reference/specs-agent-platform").exists()
