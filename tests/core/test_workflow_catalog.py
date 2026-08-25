from __future__ import annotations

import pytest
from open_workflow_agent.errors import UnsupportedWorkflowFeature
from open_workflow_agent.workflow_catalog import WorkflowCatalog


def _workflow(version="1.0.0"):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "catalog-tests",
            "name": "child",
            "version": version,
        },
        "do": [{"finish": {"set": {"ok": True}}}],
    }


def test_local_workflow_catalog_resolves_explicit_and_latest_versions():
    catalog = WorkflowCatalog()
    plan = catalog.register(_workflow())
    assert (
        catalog.resolve({"namespace": "catalog-tests", "name": "child", "version": "1.0.0"}) == plan
    )
    assert (
        catalog.resolve({"namespace": "catalog-tests", "name": "child", "version": "latest"})
        == plan
    )


def test_local_workflow_catalog_rejects_unregistered_reference():
    catalog = WorkflowCatalog()
    with pytest.raises(UnsupportedWorkflowFeature):
        catalog.resolve({"namespace": "missing", "name": "child", "version": "1.0.0"})
