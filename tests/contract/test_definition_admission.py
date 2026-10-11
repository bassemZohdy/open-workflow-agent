"""S1-01: the same pinned definition admission applies to every adapter."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from engine_cases import engine_cases
from open_workflow_agent.errors import (
    UnsupportedWorkflowFeature,
    WorkflowSchemaError,
    WorkflowSemanticError,
)
from open_workflow_agent.workflow import (
    _official_schema,
    compile_workflow,
    generate_default_workflow,
)
from open_workflow_agent.workflow_profile import SCHEMA_REVISION, SCHEMA_SHA256, workflow_profile


def test_reviewed_schema_and_profile_are_identical_in_source_package_and_capabilities():
    root = Path(__file__).resolve().parents[2]
    for path in (
        root / "resources/open-workflow/1.0.3/workflow.yaml",
        root / "core/src/open_workflow_agent/_resources/open-workflow/1.0.3/workflow.yaml",
    ):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == SCHEMA_SHA256
    for _, engine_type in engine_cases():
        assert engine_type().capabilities().as_dict()["workflowProfile"] == workflow_profile()
    assert workflow_profile()["releaseStatus"] == "prerelease"
    assert workflow_profile()["schemaRevision"] == SCHEMA_REVISION


@pytest.mark.parametrize(
    "field,value",
    [
        ("dsl", "1.0.1"),
        ("namespace", 1),
        ("name", []),
        ("version", "not-semver"),
        ("title", False),
        ("summary", []),
        ("tags", []),
        ("metadata", "text"),
    ],
)
def test_document_fields_reject_invalid_schema(field, value):
    workflow = generate_default_workflow()
    workflow["document"][field] = value
    with pytest.raises(WorkflowSchemaError):
        compile_workflow(workflow)


def test_document_metadata_is_preserved_and_plan_cannot_be_mutated():
    workflow = generate_default_workflow()
    workflow["document"].update(
        title="Title", summary="Summary", tags={"owner": "test"}, metadata={"x": [1, None]}
    )
    workflow["do"][0]["respond"]["metadata"] = {"description": "Respond"}
    plan = compile_workflow(workflow)
    assert plan.source == workflow
    workflow["document"]["name"] = "changed"
    plan.source["document"]["name"] = "also-changed"
    assert plan.name == "default-agent"
    assert plan.tasks[0].definition["metadata"] == {"description": "Respond"}


@pytest.mark.parametrize(
    "use",
    [
        {"functions": {"echo": {"set": {"ok": True}}}},
        {"errors": {"failure": {"type": "urn:test:error", "status": 500}}},
        {"retries": {"retry": {"limit": {"attempt": {"count": 2}}}}},
        {"timeouts": {"short": {"after": {"seconds": 2}}}},
        {"secrets": ["private-key"]},
        {"authentications": {"auth": {"bearer": {"token": "secret-value"}}}},
        {"extensions": [{"logging": {"extend": "all", "before": [{"log": {"set": {"x": 1}}}]}}]},
    ],
)
def test_unused_reusable_definitions_fail_closed(use):
    workflow = generate_default_workflow()
    workflow["use"] = use
    with pytest.raises(UnsupportedWorkflowFeature) as failure:
        compile_workflow(workflow)
    assert failure.value.details["reference"].startswith("/use/")
    assert "secret-value" not in str(failure.value)


@pytest.mark.parametrize(
    "evaluation,reference",
    [
        ({"language": "jsonata"}, "/evaluate/language"),
        ({"mode": "loose"}, "/evaluate/mode"),
    ],
)
def test_unsupported_expression_configuration_is_rejected(evaluation, reference):
    workflow = generate_default_workflow()
    workflow["evaluate"] = evaluation
    with pytest.raises(UnsupportedWorkflowFeature) as failure:
        compile_workflow(workflow)
    assert failure.value.details == {"reference": reference}


@pytest.mark.parametrize("location", ["input", "output", "export"])
@pytest.mark.parametrize(
    "schema",
    [
        {"resource": {"endpoint": "https://untrusted.test/schema"}},
        {"format": "avro", "document": {"type": "object"}},
        {"document": {"$ref": "https://untrusted.test/schema"}},
    ],
)
def test_nested_data_schema_admission_never_fetches_or_ignores_unsupported_schema(location, schema):
    workflow = generate_default_workflow()
    workflow["do"] = [
        {"group": {"do": [{"child": {"set": {"ok": True}, location: {"schema": schema}}}]}}
    ]
    with pytest.raises(UnsupportedWorkflowFeature) as failure:
        compile_workflow(workflow)
    assert failure.value.details["reference"].startswith(
        f"/do/0/group/do/0/child/{location}/schema"
    )


def test_invalid_inline_json_schema_is_rejected_before_execution():
    workflow = generate_default_workflow()
    workflow["input"] = {"schema": {"document": {"type": "imaginary"}}}
    with pytest.raises(WorkflowSemanticError, match="invalid inline"):
        compile_workflow(workflow)


@pytest.mark.parametrize(
    "document", [True, False, {"$defs": {"value": {"type": "object"}}, "$ref": "#/$defs/value"}]
)
@pytest.mark.asyncio
async def test_inline_boolean_and_local_fragment_schemas_are_enforced(services, document):
    from open_workflow_agent.workflow import WorkflowExecutor

    workflow = generate_default_workflow()
    workflow["input"] = {"schema": {"format": "json:2020-12", "document": document}}
    workflow["do"] = [{"finish": {"set": {"ok": True}}}]
    plan = compile_workflow(workflow)
    executor = WorkflowExecutor(services.catalog, services=services)
    if document is False:
        with pytest.raises(WorkflowSemanticError, match="workflow input"):
            await executor.execute(plan, {})
    else:
        assert await executor.execute(plan, {}) == {"ok": True}


@pytest.mark.parametrize("unit", ["days", "hours", "minutes", "seconds", "milliseconds"])
def test_all_admitted_duration_units_are_preserved(unit):
    workflow = generate_default_workflow()
    workflow["do"] = [{"pause": {"wait": {unit: 1}}}]
    assert compile_workflow(workflow).tasks[0].definition["wait"] == {unit: 1}


@pytest.mark.parametrize("mode", ["data", "envelope", "raw"])
def test_admitted_listener_read_modes_are_preserved(mode):
    workflow = generate_default_workflow()
    workflow["do"] = [
        {"event": {"listen": {"read": mode, "to": {"one": {"with": {"type": "test"}}}}}}
    ]
    assert compile_workflow(workflow).tasks[0].definition["listen"]["read"] == mode


def test_listener_iterator_at_its_official_task_location_is_rejected():
    workflow = generate_default_workflow()
    workflow["do"] = [
        {
            "awaitEvent": {
                "listen": {"to": {"one": {"with": {"type": "test"}}}},
                "foreach": {"do": [{"consume": {"set": {"ok": True}}}]},
            }
        }
    ]
    with pytest.raises(UnsupportedWorkflowFeature, match="foreach"):
        compile_workflow(workflow)


@pytest.mark.parametrize(
    "run",
    [
        {"workflow": {"namespace": "test", "name": "child", "version": "1.0.0"}, "await": False},
        {"workflow": {"namespace": "test", "name": "child", "version": "1.0.0"}, "return": "all"},
        {"container": {"image": "untrusted/image"}},
    ],
)
def test_unimplemented_run_modes_are_rejected(run):
    workflow = generate_default_workflow()
    workflow["do"] = [{"child": {"run": run}}]
    with pytest.raises(UnsupportedWorkflowFeature):
        compile_workflow(workflow)


def test_duplicate_task_names_are_rejected_in_nested_scope():
    workflow = generate_default_workflow()
    workflow["do"] = [{"group": {"do": [{"same": {"set": {"x": 1}}}, {"same": {"set": {"x": 2}}}]}}]
    with pytest.raises(WorkflowSemanticError, match="duplicate"):
        compile_workflow(workflow)


@pytest.mark.parametrize(
    "extension,content",
    [
        ("yaml", "document: {}\ndocument: {}\ndo: []\n"),
        ("json", '{"document":{},"document":{},"do":[]}'),
        ("yaml", "document: {}\ndo: []\nextra: &cycle [*cycle]\n"),
        ("yaml", "document: {}\ndo: []\nextra: 2026-10-11\n"),
        ("yaml", "document: {}\ndo: []\nextra: .nan\n"),
    ],
)
def test_ambiguous_or_non_json_workflow_files_are_rejected(tmp_path, extension, content):
    path = tmp_path / f"workflow.{extension}"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(WorkflowSchemaError):
        compile_workflow(path)


def test_schema_override_cannot_replace_the_reviewed_pin(tmp_path, monkeypatch):
    path = tmp_path / "schema.yaml"
    path.write_text("type: object\n", encoding="utf-8")
    monkeypatch.setenv("OWA_SCHEMA_PATH", str(path))
    _official_schema.cache_clear()
    try:
        with pytest.raises(WorkflowSchemaError, match="SHA-256"):
            compile_workflow()
    finally:
        _official_schema.cache_clear()


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_admission_preserves_json_and_yaml_outcomes(
    tmp_path, services, engine_name, engine_type
):
    import yaml

    workflow = generate_default_workflow()
    workflow["evaluate"] = {"language": "jq", "mode": "strict"}
    workflow["do"] = [{"complete": {"set": {"ok": True, "values": [None, 1, "text"]}}}]
    engine = engine_type()
    await engine.initialize(services)
    try:
        for extension, text in (("json", json.dumps(workflow)), ("yaml", yaml.safe_dump(workflow))):
            path = tmp_path / f"workflow.{extension}"
            path.write_text(text, encoding="utf-8")
            plan = compile_workflow(path)
            assert plan.source == copy.deepcopy(workflow)
            handle = services.invocations.create(
                engine=engine_name,
                session_id=None,
                user_id=None,
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            result = await engine.invoke(await engine.compile(plan), handle, {})
            assert result.status == "completed"
            assert result.output == {"ok": True, "values": [None, 1, "text"]}
    finally:
        await engine.shutdown()
