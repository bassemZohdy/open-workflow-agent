from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("name", "expected_output_type"),
    [("agent", None), ("llm", "string")],
)
def test_builtin_catalog_manifests_declare_valid_io_schemas(
    name: str, expected_output_type: str | None
) -> None:
    path = ROOT / "runtime-catalog" / "functions" / name / "1.0.0" / "function.yaml"
    manifest: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert manifest["name"] == name
    assert manifest["version"] == "1.0.0"
    assert manifest["namespace"] == "default"
    assert isinstance(manifest["input"], dict)
    assert isinstance(manifest["output"], dict)
    Draft202012Validator.check_schema(manifest["input"])
    Draft202012Validator.check_schema(manifest["output"])
    if expected_output_type is not None:
        assert manifest["output"]["type"] == expected_output_type
