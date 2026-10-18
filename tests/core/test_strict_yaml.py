from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from open_workflow_agent.errors import WorkflowSchemaError
from open_workflow_agent.strict_yaml import load_strict_yaml
from open_workflow_agent.workflow import load_workflow

_HEADER = """document:
  dsl: '1.0.3'
  namespace: test
  name: strict
  version: '1.0.0'
"""


def test_duplicate_top_level_key_is_rejected() -> None:
    with pytest.raises(yaml.YAMLError, match="duplicate key 'a'"):
        load_strict_yaml("a: 1\na: 2\n")


def test_duplicate_nested_key_is_rejected() -> None:
    with pytest.raises(yaml.YAMLError, match="duplicate key 'x'"):
        load_strict_yaml("do:\n  - t:\n      set:\n        x: 1\n        x: 2\n")


def test_same_key_in_sibling_mappings_is_allowed() -> None:
    assert load_strict_yaml("a: {x: 1}\nb: {x: 2}\n") == {"a": {"x": 1}, "b": {"x": 2}}


def test_merge_keys_still_work() -> None:
    text = "base: &b {x: 1}\nderived:\n  <<: *b\n  y: 2\n"
    assert load_strict_yaml(text)["derived"] == {"x": 1, "y": 2}


def test_load_workflow_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yaml"
    path.write_text(
        _HEADER + "do:\n  - a:\n      set:\n        x: 1\n        x: 2\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowSchemaError, match="duplicate key 'x'"):
        load_workflow(path)


def test_load_workflow_accepts_unique_keys(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yaml"
    path.write_text(_HEADER + "do:\n  - a:\n      set:\n        x: 1\n", encoding="utf-8")
    assert load_workflow(path)["do"][0]["a"]["set"] == {"x": 1}
