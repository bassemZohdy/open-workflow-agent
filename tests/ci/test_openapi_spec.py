"""Keep the versioned HTTP API schema aligned with the runtime."""

from __future__ import annotations

import json
from pathlib import Path

from open_workflow_agent.api import create_app

SPEC_PATH = Path(__file__).parents[2] / "docs" / "openapi.json"


def test_versioned_openapi_spec_matches_runtime() -> None:
    versioned = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    runtime = create_app().openapi()

    assert versioned == runtime
    assert versioned["info"]["version"] == "0.1.0"
    assert "/metrics" in versioned["paths"]
