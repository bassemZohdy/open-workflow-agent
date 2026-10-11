from __future__ import annotations

import pytest
from open_workflow_agent.errors import (
    UnsupportedWorkflowFeature,
    WorkflowExecutionError,
    WorkflowSemanticError,
)
from open_workflow_agent.versions import version_precedence
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


def test_latest_resolves_semantic_precedence_independently_of_registration_order():
    catalog = WorkflowCatalog()
    versions = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
        "1.2.0",
        "1.10.0",
    ]
    keys = [version_precedence(version) for version in versions]
    assert keys == sorted(keys)
    for version in reversed(versions):
        catalog.register(_workflow(version))
    assert catalog.resolve({"namespace": "catalog-tests", "name": "child"}).version == "1.10.0"
    assert (
        catalog.resolve(
            {"namespace": "catalog-tests", "name": "child", "version": "1.0.0-beta.2"}
        ).version
        == "1.0.0-beta.2"
    )


def test_build_metadata_precedence_ties_require_an_explicit_version():
    catalog = WorkflowCatalog()
    one = catalog.register(_workflow("1.0.0+one"))
    catalog.register(_workflow("1.0.0+two"))
    assert (
        catalog.resolve({"namespace": "catalog-tests", "name": "child", "version": "1.0.0+one"})
        == one
    )
    with pytest.raises(WorkflowExecutionError, match="ambiguous"):
        catalog.resolve({"namespace": "catalog-tests", "name": "child"})


def test_workflow_identity_cannot_be_overwritten_with_different_content():
    catalog = WorkflowCatalog()
    original = _workflow()
    plan = catalog.register(original)
    assert catalog.register(original) == plan
    altered = _workflow()
    altered["do"] = [{"finish": {"set": {"ok": False}}}]
    with pytest.raises(WorkflowSemanticError, match="immutable"):
        catalog.register(altered)


@pytest.mark.parametrize(
    "value",
    [
        "01.0.0",
        "1.0",
        "1.0.0-01",
        "1.0.0-",
        "1.0.0+",
        "v1.0.0",
        "1.0.0\n",
        "１.0.0",
        "1" * 257 + ".0.0",
    ],
)
def test_invalid_semantic_versions_are_rejected(value):
    assert version_precedence(value) is None
