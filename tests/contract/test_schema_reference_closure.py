from __future__ import annotations

import pytest
from open_workflow_agent.errors import UnsupportedWorkflowFeature, WorkflowSemanticError
from open_workflow_agent.workflow import (
    WorkflowExecutor,
    compile_workflow,
    generate_default_workflow,
)


def _workflow(document):
    workflow = generate_default_workflow()
    workflow["input"] = {"schema": {"document": document}}
    workflow["do"] = [{"finish": {"set": {"ok": True}}}]
    return workflow


@pytest.mark.parametrize(
    "document",
    [
        {"$ref": "#/missing"},
        {"title": "ordinary string", "$ref": "#/title"},
        {"$dynamicRef": "#absent"},
        {"properties": {"value": {"$ref": "#/$defs/absent"}}},
    ],
)
def test_missing_or_non_schema_targets_fail_during_definition_admission(document):
    with pytest.raises(WorkflowSemanticError, match="inline schema reference") as failure:
        compile_workflow(_workflow(document))
    assert failure.value.details["reference"].startswith("/input/schema/document/")


@pytest.mark.parametrize(
    "document,data",
    [
        ({"$defs": {"a/b~c": {"type": "string"}}, "$ref": "#/$defs/a~1b~0c"}, "text"),
        ({"$defs": {"integer": {"$anchor": "number", "type": "integer"}}, "$ref": "#number"}, 3),
        (
            {
                "$defs": {"integer": {"$dynamicAnchor": "number", "type": "integer"}},
                "$dynamicRef": "#number",
            },
            3,
        ),
        ({"$defs": {"integer": {"type": "integer"}}, "$ref": "#/%24defs/integer"}, 3),
        (
            {
                "$id": "https://schemas.test/root",
                "$defs": {
                    "child": {
                        "$id": "child",
                        "$defs": {"integer": {"type": "integer"}},
                        "$ref": "#/$defs/integer",
                    }
                },
                "$ref": "#/$defs/child",
            },
            3,
        ),
        (
            {"const": {"$ref": "https://ordinary-data.test", "$schema": "data"}},
            {"$ref": "https://ordinary-data.test", "$schema": "data"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_fragment_reference_closure_and_literal_data_are_network_free(
    services, document, data
):
    plan = compile_workflow(_workflow(document))
    assert await WorkflowExecutor(services.catalog, services=services).execute(plan, data) == {
        "ok": True
    }


def test_other_inline_schema_dialects_fail_the_capability_gate():
    with pytest.raises(UnsupportedWorkflowFeature, match="schema dialect"):
        compile_workflow(_workflow({"$schema": "http://json-schema.org/draft-07/schema#"}))
