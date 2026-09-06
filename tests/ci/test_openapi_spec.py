"""Keep the versioned HTTP API schema aligned with the runtime."""

from __future__ import annotations

import json
from pathlib import Path

from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices

SPEC_PATH = Path(__file__).parents[2] / "docs" / "openapi.json"


def test_versioned_openapi_spec_matches_runtime(tmp_path: Path) -> None:
    versioned = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    try:
        runtime = create_app(config=config, services=services).openapi()
    finally:
        services.close()

    assert versioned == runtime
    assert versioned["info"]["version"] == "0.1.0"
    assert "/metrics" in versioned["paths"]
