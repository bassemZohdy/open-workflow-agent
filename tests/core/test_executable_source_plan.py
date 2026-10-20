"""Compiler/archive source capture is pure and preserves public source pointers."""

from __future__ import annotations

import copy
import hashlib

import pytest
from open_workflow_agent import recovery_definitions
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.effects import EffectJournal
from open_workflow_agent.errors import (
    ConfigurationError,
    OwaError,
    UnsupportedWorkflowFeature,
    WorkflowExecutionError,
    WorkflowSemanticError,
)
from open_workflow_agent.executable_source_plan import (
    capture_script_resources,
    materialize_script_resources,
)
from open_workflow_agent.executable_sources import (
    ExecutableScriptBinding,
    ExecutableSource,
    ExecutableSourceRegistry,
)
from open_workflow_agent.sandbox import compile_sandbox_workflow

URI = "https://approved.test/source.py"
CODE = b"# retained exact source\r\nprint('captured')\r\n"


def artifact(code=CODE):
    return ExecutableSource.capture(
        id="original", version="1.0.0", uri=URI, code=code, digest=hashlib.sha256(code).hexdigest()
    )


def script():
    return {"run": {"script": {"language": "python", "source": {"endpoint": {"uri": URI}}}}}


def workflow(tasks, **changes):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "source-plan",
            "name": "original",
            "version": "1.0.0",
        },
        "do": tasks,
        **changes,
    }


def compile_original(public, source=None):
    source = source or artifact()
    return compile_sandbox_workflow(
        public,
        sandbox=SandboxConfig(enabled=True),
        executable_sources=ExecutableSourceRegistry((source,)),
    )


@pytest.mark.parametrize("budget,supported", [(10_485_760, True), (11_534_336, False)])
def test_source_output_profile_rejects_before_plan_admission(budget, supported):
    public = workflow([{"action": script()}])
    original = copy.deepcopy(public)
    policy = SandboxConfig(enabled=True, max_output_bytes=budget)
    registry = ExecutableSourceRegistry((artifact(),))
    if supported:
        assert compile_sandbox_workflow(
            public, sandbox=policy, executable_sources=registry
        ).script_resources
    else:
        with pytest.raises(UnsupportedWorkflowFeature) as raised:
            compile_sandbox_workflow(public, sandbox=policy, executable_sources=registry)
        assert raised.value.details["reason"] == "executable_source_output_profile_unsupported"
    assert public == original


@pytest.mark.parametrize("kind", ["direct", "nested", "fork", "reusable"])
def test_capture_materializes_private_code_without_changing_public_source_or_pointer(kind):
    task = script()
    if kind == "direct":
        public, reference = workflow([{"action": task}]), "/do/0/action"
    elif kind == "nested":
        public, reference = (
            workflow([{"group": {"do": [{"action": task}]}}]),
            "/do/0/group/do/0/action",
        )
    elif kind == "fork":
        public, reference = (
            workflow([{"group": {"fork": {"branches": [{"branch": {"do": [{"action": task}]}}]}}}]),
            "/do/0/group/fork/branches/0/branch/do/0/action",
        )
    else:
        public = workflow(
            [{"one": {"call": "approvedFunction"}}, {"two": {"call": "approvedFunction"}}],
            use={"functions": {"approvedFunction": task}},
        )
        reference = "/use/functions/approvedFunction"
    untouched = copy.deepcopy(public)
    captured = capture_script_resources(public, ExecutableSourceRegistry((artifact(),)))
    assert captured == (ExecutableScriptBinding(reference, artifact()),)
    plan = compile_original(public)
    assert public == untouched and plan.source == untouched
    assert plan.script_resources == captured
    runs = [task for scope in plan.scopes for task in scope.tasks if task.kind == "run"]
    assert len(runs) == (2 if kind == "reusable" else 1)
    assert all(task.reference == reference for task in runs)
    assert all(task.definition["run"]["script"]["code"].encode() == CODE for task in runs)
    assert all("source" not in task.definition["run"]["script"] for task in runs)
    if kind == "reusable":
        assert {task.execution_reference for task in runs} == {
            "/do/0/one/call/approvedFunction",
            "/do/1/two/call/approvedFunction",
        }
    restored = recovery_definitions._plan(recovery_definitions._definition(plan))
    assert restored == plan and restored.source == untouched


@pytest.mark.parametrize(
    "scope,reference",
    [
        ("do", "/do/0/group/do/0/name~1with~0escape"),
        ("try", "/do/0/group/try/0/name~1with~0escape"),
        ("catch", "/do/0/group/catch/do/0/name~1with~0escape"),
        ("foreach", "/do/0/group/foreach/do/0/name~1with~0escape"),
    ],
)
def test_capture_uses_existing_canonical_nested_scope_and_name_escaping(scope, reference):
    children = [{"name/with~escape": script()}]
    group = (
        {scope: children}
        if scope in {"do", "try"}
        else {"catch": {"do": children}}
        if scope == "catch"
        else {"listen": {}, "foreach": {"do": children}}
    )
    captured = capture_script_resources(
        workflow([{"group": group}]), ExecutableSourceRegistry((artifact(),))
    )
    assert captured[0].reference == reference


@pytest.mark.parametrize(
    "damage", ["missing", "orphan", "duplicate", "wrong-uri", "wrong-language", "mixed"]
)
def test_materialization_rejects_missing_or_foreign_binding_before_deriving_code(damage):
    public = workflow([{"action": script()}])
    binding = ExecutableScriptBinding("/do/0/action", artifact())
    bindings = (binding,)
    if damage == "missing":
        bindings = ()
    elif damage == "orphan":
        bindings = (ExecutableScriptBinding("/do/0/other", artifact()),)
    elif damage == "duplicate":
        bindings = (binding, binding)
    elif damage == "wrong-uri":
        public["do"][0]["action"]["run"]["script"]["source"]["endpoint"]["uri"] = (
            "https://approved.test/changed.py"
        )
    elif damage == "wrong-language":
        public["do"][0]["action"]["run"]["script"]["language"] = "js"
    else:
        public["do"][0]["action"]["run"]["script"]["code"] = "must not supersede capture"
    untouched = copy.deepcopy(public)
    with pytest.raises((WorkflowSemanticError, UnsupportedWorkflowFeature)):
        materialize_script_resources(public, bindings)
    assert public == untouched


@pytest.mark.parametrize(
    "uri", ["https://approved.test/undeclared.py", "${ .source }", "file:///private/source.py"]
)
def test_compile_never_fetches_or_adopts_undeclared_source(uri):
    public = workflow([{"action": script()}])
    public["do"][0]["action"]["run"]["script"]["source"]["endpoint"]["uri"] = uri
    with pytest.raises(OwaError):
        compile_original(public)


def test_mixed_inline_and_external_source_rejects_at_pure_capture():
    public = workflow([{"action": script()}])
    public["do"][0]["action"]["run"]["script"]["code"] = "inline replacement"
    with pytest.raises(WorkflowSemanticError):
        capture_script_resources(public, ExecutableSourceRegistry((artifact(),)))


def test_unexpected_registry_failure_is_not_misreported_as_user_source_rejection(monkeypatch):
    public = workflow([{"action": script()}])

    def broken_registry(*args, **kwargs):
        raise RuntimeError("internal source registry defect")

    monkeypatch.setattr(ExecutableSourceRegistry, "resolve", broken_registry)
    with pytest.raises(RuntimeError, match="internal source registry defect"):
        capture_script_resources(public, ExecutableSourceRegistry((artifact(),)))


@pytest.mark.parametrize(
    "failure",
    [
        ConfigurationError("invalid registry state"),
        WorkflowExecutionError("captured source evidence is corrupt"),
    ],
)
def test_registry_owa_errors_outside_source_rejection_propagate_unchanged(monkeypatch, failure):
    public = workflow([{"action": script()}])

    def broken_registry(*args, **kwargs):
        raise failure

    monkeypatch.setattr(ExecutableSourceRegistry, "resolve", broken_registry)
    with pytest.raises(type(failure)) as raised:
        capture_script_resources(public, ExecutableSourceRegistry((artifact(),)))
    assert raised.value is failure


def test_changed_capture_changes_fingerprint_but_keeps_original_public_document():
    public = workflow([{"action": script()}])
    original = compile_original(public)
    changed = compile_original(public, artifact(b"print('different source')\n"))
    assert original.source == changed.source == public
    assert original.fingerprint != changed.fingerprint
    assert original.script_resources[0].source.verified_bytes() == CODE


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "orphan",
        "duplicate",
        "code-hash",
        "new-valid-code",
        "original-uri",
        "execution-only-field",
    ],
)
def test_archive_rejects_changed_source_proof_before_effect_intent_or_native_projection(
    monkeypatch, damage
):
    plan = compile_original(workflow([{"action": script()}]))
    saved = recovery_definitions._definition(plan)

    def forbidden(*args, **kwargs):
        raise AssertionError("archive source verification created an execution intent")

    monkeypatch.setattr(EffectJournal, "begin", forbidden)
    monkeypatch.setattr(recovery_definitions, "insert_completed_receipt", forbidden)
    assert recovery_definitions._plan(copy.deepcopy(saved)) == plan
    if damage == "missing":
        saved.pop("script_resources")
    elif damage == "orphan":
        saved["script_resources"][0]["reference"] = "/do/0/other"
    elif damage == "duplicate":
        saved["script_resources"].append(copy.deepcopy(saved["script_resources"][0]))
    elif damage == "code-hash":
        saved["script_resources"][0]["source"]["code"] = "altered without matching integrity"
    elif damage == "new-valid-code":
        code = b"print('altered with matching integrity')\n"
        saved["script_resources"][0]["source"] = artifact(code).as_value()
    elif damage == "original-uri":
        saved["source"]["do"][0]["action"]["run"]["script"]["source"]["endpoint"]["uri"] = (
            "https://approved.test/changed.py"
        )
    else:
        saved["script_resources"][0]["execution_reference"] = "/private/derived"
    with pytest.raises(OwaError):
        recovery_definitions._plan(saved)


def test_legacy_inline_plan_has_no_new_source_fields_or_capture_dependency():
    public = workflow(
        [{"action": {"run": {"script": {"language": "python", "code": "print('inline')"}}}}]
    )
    plan = compile_sandbox_workflow(public, sandbox=SandboxConfig(enabled=True))
    assert plan.script_resources == () and plan.source == public
    saved = recovery_definitions._definition(plan)
    assert "script_resources" not in saved
    assert recovery_definitions._plan(saved) == plan
