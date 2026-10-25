"""Original definition admission retains finite inputs and rejects reinterpretation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import ExternalCatalogConfig, ExternalResourceConfig, RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency, effect_request_digest
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import (
    InvocationStateConflict,
    InvocationStateError,
    WorkflowDefinitionChanged,
    WorkflowExecutionError,
)
from open_workflow_agent.external_catalog import ExternalCatalogResolver, ResolvedCatalogFunction
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.recovery_definitions import (
    PROFILE,
    RecoveryDefinitionArchive,
    admission_identity,
)
from open_workflow_agent.resources import SchemaResource
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import (
    compile_workflow,
    generate_default_workflow,
    normalize_workflow,
    resolve_and_compile_workflow,
)
from pydantic import ValidationError


@pytest.fixture(params=["sqlite", "postgresql"])
def archive_tree(tmp_path, request):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    manifest = {
        "profile": PROFILE,
        "backend": request.param,
        "artifact": "a" * 64,
        "sdk": "qualified-test-profile",
        "continuation_supported": True,
    }
    archive = RecoveryDefinitionArchive(
        services.effect_journal,
        services.invocations,
        profile_provider=lambda engine: {**copy.deepcopy(manifest), "engine": engine},
    )
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                # A corruption case owns its content-addressed archive. Shared
                # PostgreSQL fixtures must not poison another invocation's plan.
                "name": "retained-" + uuid4().hex,
                "version": "1.0.0",
            },
            "do": [{"pause": {"wait": {"seconds": 60}}}],
        }
    )
    parent = services.invocations.create(
        engine="reference",
        session_id=None,
        user_id="correlation",
        owner_principal="owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    parent._execution_lease = services.execution_ownership.acquire(parent, seconds=300)
    try:
        yield services, archive, parent, plan, manifest
    finally:
        services.execution_ownership.release(parent._execution_lease)
        services.close()


def _admit(tree, value=None):
    _, archive, parent, plan, _ = tree
    archive.admit(plan, parent, value, execution_lease=parent._execution_lease)


def _rows(tree):
    services, _, parent, _, _ = tree
    connection = services.effect_journal.connection
    return (
        connection.execute("SELECT * FROM recovery_definitions ORDER BY fingerprint").fetchall(),
        connection.execute(
            "SELECT * FROM recovery_invocation_definitions ORDER BY invocation_id"
        ).fetchall(),
        connection.execute(
            "SELECT * FROM invocation_inputs WHERE invocation_id=?", (parent.invocation_id,)
        ).fetchall(),
        connection.execute(
            "SELECT * FROM invocation_ownership WHERE invocation_id=?", (parent.invocation_id,)
        ).fetchall(),
        connection.execute(
            "SELECT * FROM workflow_effect_invocations WHERE invocation_id=? ORDER BY identity",
            (parent.invocation_id,),
        ).fetchall(),
        connection.execute(
            "SELECT e.* FROM workflow_effects e "
            "JOIN workflow_effect_invocations i ON i.identity=e.identity "
            "WHERE i.invocation_id=? ORDER BY e.identity",
            (parent.invocation_id,),
        ).fetchall(),
    )


@pytest.mark.parametrize("original", [None, {"original": "value"}, [1, False, None]])
def test_original_public_definition_input_and_auth_binding_survive_new_deployment(
    archive_tree, original
):
    services, archive, parent, plan, _ = archive_tree
    assert not archive.has_binding(parent)
    _admit(archive_tree, original)
    before = _rows(archive_tree)
    replacement = plan.source
    replacement["document"]["version"] = "2.0.0"
    replacement["do"] = [{"new": {"set": {"version": 2}}}]
    services.workflow_catalog.register(replacement)
    recovered = archive.resolve(parent)
    assert recovered.source == plan.source and recovered.fingerprint == plan.fingerprint
    assert services.invocations.read_input(parent) == original
    assert archive.has_binding(parent)
    archive.verify(parent, plan)
    _admit(archive_tree, original)
    with pytest.raises(WorkflowDefinitionChanged):
        archive.verify(parent, compile_workflow(replacement))
    assert _rows(archive_tree) == before and services.model.calls == []


def test_captured_catalog_and_schema_are_rebuilt_without_a_resolver(archive_tree):
    services, archive, parent, _, _ = archive_tree
    source = generate_default_workflow()
    source["use"] = {"catalogs": {"trusted": {"endpoint": "https://catalog.test/root"}}}
    source["do"] = [{"remote": {"call": "echo:1.0.0@trusted"}}]
    definition = {
        "call": "http",
        "with": {"method": "POST", "endpoint": "https://service.test/echo"},
    }
    document = {"type": "object", "properties": {"original": {"const": True}}}
    captured = ResolvedCatalogFunction(
        "echo:1.0.0@trusted",
        "https://catalog.test/root/echo/1.0.0",
        "b" * 64,
        json.dumps(definition),
        json.dumps(definition),
    )
    resource = SchemaResource(
        "https://schema.test/original",
        "c" * 64,
        json.dumps(document),
        len(json.dumps(document).encode()),
    )
    plan = normalize_workflow(source, imports=(captured,), resources=(resource,))
    new = services.invocations.create(
        engine="reference",
        session_id=None,
        user_id=None,
        owner_principal="owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(new, seconds=300)
    try:
        archive.admit(plan, new, {"original": True}, execution_lease=lease)
        recovered = archive.resolve(new)
        assert recovered.fingerprint == plan.fingerprint
        assert recovered.imports[0].definition == definition
        assert recovered.imports[0].digest == "b" * 64
        assert recovered.resources[0].document == document
        assert recovered.resources[0].digest == "c" * 64
        # Changing the live object returned to a caller cannot mutate stored content.
        recovered.imports[0].definition["with"]["method"] = "DELETE"
        assert archive.resolve(new).imports[0].definition == definition
        assert not services.model.calls and not archive.has_binding(parent)
    finally:
        services.execution_ownership.release(lease)


@pytest.mark.parametrize(
    "damage,reason",
    [
        ("archive-missing", "archive_missing"),
        ("archive-digest", "archive_invalid"),
        ("binding-missing", "binding_missing"),
        ("binding-digest", "binding_invalid"),
        ("input-missing", "input_missing"),
        ("input-changed", "input_changed"),
        ("profile", "runtime_profile_changed"),
        ("owner", "binding_invalid"),
    ],
)
def test_missing_or_changed_evidence_rejects_pure_resolution_before_mutation(
    archive_tree, damage, reason
):
    services, archive, parent, plan, manifest = archive_tree
    _admit(archive_tree, {"original": True})
    connection = services.effect_journal.connection
    selected = parent
    if damage == "archive-missing":
        connection.execute(
            "DELETE FROM recovery_definitions WHERE fingerprint=?", (plan.fingerprint,)
        )
    elif damage == "archive-digest":
        connection.execute(
            "UPDATE recovery_definitions SET digest='changed' WHERE fingerprint=?",
            (plan.fingerprint,),
        )
    elif damage == "binding-missing":
        connection.execute(
            "DELETE FROM recovery_invocation_definitions WHERE invocation_id=?",
            (parent.invocation_id,),
        )
    elif damage == "binding-digest":
        connection.execute(
            "UPDATE recovery_invocation_definitions SET digest='changed' WHERE invocation_id=?",
            (parent.invocation_id,),
        )
    elif damage == "input-missing":
        connection.execute(
            "DELETE FROM invocation_inputs WHERE invocation_id=?", (parent.invocation_id,)
        )
    elif damage == "input-changed":
        connection.execute(
            "UPDATE invocation_inputs SET payload='null' WHERE invocation_id=?",
            (parent.invocation_id,),
        )
    elif damage == "profile":
        manifest["sdk"] = "unqualified-new-format"
    else:
        selected = replace(parent, owner_principal="another-owner")
    connection.commit()
    before = _rows(archive_tree)
    with pytest.raises(InvocationStateError) as caught:
        archive.resolve(selected)
    assert caught.value.details["reason"] == reason
    snapshot = archive.diagnostic_safe_snapshot(selected)
    assert snapshot["status"] == "unavailable" and snapshot["reason"] == reason
    assert _rows(archive_tree) == before and services.model.calls == []
    assert not any(
        key in snapshot
        for key in (
            "input",
            "source",
            "runtime_profile",
            "owner_principal",
            "engine_execution_reference",
        )
    )


def test_atomic_admission_failure_rolls_back_definition_binding_and_original_input(
    archive_tree, monkeypatch
):
    _, archive, _, _, _ = archive_tree
    before = _rows(archive_tree)

    def fail(*args, **kwargs):
        raise InvocationStateError("injected admission failure")

    monkeypatch.setattr(archive, "_read", fail)
    with pytest.raises(InvocationStateError, match="injected"):
        _admit(archive_tree, {"original": True})
    assert _rows(archive_tree) == before


def test_stale_execution_generation_and_changed_prebound_input_cannot_admit(archive_tree):
    services, archive, parent, plan, _ = archive_tree
    with pytest.raises(InvocationStateConflict):
        archive.admit(
            plan,
            parent,
            None,
            execution_lease=replace(
                parent._execution_lease, generation=parent._execution_lease.generation + 1
            ),
        )
    assert not archive.has_binding(parent)
    services.invocations.bind_input(parent, {"original": True})
    with pytest.raises(InvocationStateError) as caught:
        _admit(archive_tree, {"changed": True})
    assert caught.value.details["reason"] == "input_changed"
    assert not archive.has_binding(parent)
    assert services.invocations.read_input(parent) == {"original": True}


def test_missing_legacy_archive_cannot_be_created_from_current_definition(archive_tree):
    services, archive, parent, plan, _ = archive_tree
    identity = EffectIdentity(
        parent.invocation_id, plan.fingerprint, "/do/0/pause", (("/do/0/pause", 1),), "call:http"
    )
    services.effect_journal.begin(
        identity,
        effect_request_digest("http", {}),
        "legacy",
        execution_lease=parent._execution_lease,
    )
    before = _rows(archive_tree)
    with pytest.raises(InvocationStateError) as caught:
        _admit(archive_tree, None)
    assert caught.value.details["reason"] == "binding_missing"
    assert _rows(archive_tree) == before
    with pytest.raises(InvocationStateError) as caught:
        archive.has_binding(parent)
    assert caught.value.details["reason"] == "binding_missing"


@pytest.mark.parametrize("missing_index", [False, True])
def test_admission_without_native_projection_cannot_look_fresh_after_binding_deletion(
    archive_tree, missing_index
):
    services, archive, parent, _, _ = archive_tree
    _admit(archive_tree, {"original": True})
    identity = admission_identity(parent)
    receipt = services.effect_journal.get(identity)
    assert receipt is not None
    dependency = ReceiptDependency.capture(identity, receipt)
    connection = services.effect_journal.connection
    connection.execute(
        "DELETE FROM recovery_invocation_definitions WHERE invocation_id=?", (parent.invocation_id,)
    )
    if missing_index:
        connection.execute(
            "DELETE FROM workflow_effect_invocations WHERE identity=?", (identity.key,)
        )
    connection.commit()
    before = _rows(archive_tree)
    with pytest.raises(InvocationStateError) as caught:
        archive.has_binding(parent)
    assert caught.value.details["reason"] == "binding_missing"
    with pytest.raises(InvocationStateError):
        _admit(archive_tree, {"original": True})
    with pytest.raises(WorkflowExecutionError):
        dependency.verify(services.effect_journal)
    assert _rows(archive_tree) == before and services.model.calls == []


@pytest.mark.parametrize("damage", ["receipt", "index"])
def test_original_definition_requires_immutable_admission_receipt_and_index(archive_tree, damage):
    services, archive, parent, _, _ = archive_tree
    _admit(archive_tree, None)
    identity = admission_identity(parent)
    connection = services.effect_journal.connection
    table = "workflow_effects" if damage == "receipt" else "workflow_effect_invocations"
    connection.execute(f"DELETE FROM {table} WHERE identity=?", (identity.key,))
    connection.commit()
    before = _rows(archive_tree)
    with pytest.raises(InvocationStateError) as caught:
        archive.resolve(parent)
    assert caught.value.details["reason"] == "binding_invalid"
    assert _rows(archive_tree) == before


def test_unclassified_deployment_options_allow_initial_execution_but_reject_continuation(
    archive_tree,
):
    _, archive, parent, _, manifest = archive_tree
    manifest["continuation_supported"] = False
    _admit(archive_tree, None)
    assert archive.verify_admission(parent).fingerprint == parent.workflow_fingerprint
    with pytest.raises(InvocationStateError) as caught:
        archive.resolve(parent)
    assert caught.value.details["reason"] == "deployment_profile_unqualified"


def test_fresh_fingerprints_can_coexist_without_reinterpreting_an_existing_invocation(archive_tree):
    services, archive, parent, plan, _ = archive_tree
    _admit(archive_tree, {"original": True})
    source = plan.source
    source["do"] = [{"changed": {"set": {"revision": 2}}}]
    second = compile_workflow(source)
    assert second.version == plan.version and second.fingerprint != plan.fingerprint
    handle = services.invocations.create(
        engine="reference",
        session_id=None,
        user_id=None,
        owner_principal="owner",
        workflow_name=second.name,
        workflow_version=second.version,
        workflow_fingerprint=second.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle, seconds=300)
    try:
        archive.admit(second, handle, None, execution_lease=lease)
        assert archive.resolve(parent).fingerprint == plan.fingerprint
        assert archive.resolve(handle).fingerprint == second.fingerprint
        with pytest.raises(WorkflowDefinitionChanged):
            archive.verify(parent, second)
    finally:
        services.execution_ownership.release(lease)


def test_revoked_deployment_trust_is_read_only_and_sanitized(archive_tree):
    _, archive, parent, _, _ = archive_tree
    _admit(archive_tree, None)
    before = _rows(archive_tree)

    def revoked(_plan):
        raise InvocationStateError("private resolver detail must not be exposed")

    archive.trust_verifier = revoked
    with pytest.raises(InvocationStateError) as caught:
        archive.resolve(parent)
    assert caught.value.details["reason"] == "deployment_trust_changed"
    snapshot = archive.diagnostic_safe_snapshot(parent)
    assert snapshot["reason"] == "deployment_trust_changed"
    assert "provenance" not in snapshot
    assert "private resolver" not in json.dumps(snapshot)
    assert _rows(archive_tree) == before


def test_safe_provenance_projects_only_verified_digests_without_mutation(archive_tree):
    _, archive, parent, _, _ = archive_tree
    _admit(archive_tree, {"private": "must-not-expose-original-input"})
    before = _rows(archive_tree)
    snapshot = archive.diagnostic_safe_snapshot(parent)
    assert snapshot["status"] == "compatible"
    _, binding = archive._read(parent)
    assert snapshot["provenance"] == {
        "definition_digest": binding["definition_digest"],
        "runtime_profile_digest": binding["runtime_profile_digest"],
    }
    assert "must-not-expose" not in json.dumps(snapshot)
    assert _rows(archive_tree) == before


def test_default_profile_never_hashes_or_retains_unclassified_secret_options(monkeypatch):
    import open_workflow_agent.recovery_definitions as module

    monkeypatch.setattr(module, "_artifact", lambda *args: {"code_sha256": "d" * 64})
    secret = "test-secret-value-that-must-not-enter-a-manifest"
    config = RuntimeConfig.model_validate(
        {
            "model": {
                "options": {"opaque_vendor_option": {"value": secret}},
                "credentials": {"api_key": {"from_env": "OWA_TEST_MODEL_API_KEY"}},
            }
        }
    )
    assert config.model.credentials["api_key"].from_env == "OWA_TEST_MODEL_API_KEY"
    value = module.recovery_profile("reference", backend="sqlite", config=config)
    encoded = json.dumps(value)
    assert not value["continuation_supported"]
    assert secret not in encoded and hashlib.sha256(secret.encode()).hexdigest() not in encoded
    old = module.recovery_profile("reference", backend="sqlite", config=RuntimeConfig())
    config = RuntimeConfig.model_validate(
        {"workflow": {"definition": {"deployment": "v2"}}, "observability": {"log_level": "DEBUG"}}
    )
    assert module.recovery_profile("reference", backend="sqlite", config=config) == old


@pytest.mark.parametrize("field", ["options", "credentials"])
def test_model_known_credentials_reject_raw_values_without_exposing_them(field):
    secret = "raw-model-credential-canary-that-must-not-enter-errors"
    with pytest.raises(ValidationError) as caught:
        RuntimeConfig.model_validate({"model": {field: {"api_key": secret}}})
    assert secret not in str(caught.value)
    assert "model" in str(caught.value)


def test_installed_wheel_provenance_has_no_checkout_or_sdk_import_requirement(
    tmp_path, monkeypatch
):
    import open_workflow_agent.recovery_definitions as module

    package = tmp_path / "installed_package"
    package.mkdir()
    (package / "__init__.py").write_text("# installed code\n")
    marker = {
        "profile": "owa/recovery-build/v1",
        "project": "test-distribution",
        "version": "1.0.0",
        "lock_sha256": "e" * 64,
        "metadata_sha256": "f" * 64,
    }
    (package / "_recovery_build.json").write_text(json.dumps(marker))
    installed = SimpleNamespace(
        version="1.0.0",
        locate_file=lambda _path: package / "_recovery_build.json",
        read_text=lambda _name: None,
    )
    monkeypatch.setattr(module.importlib.metadata, "distribution", lambda _name: installed)
    monkeypatch.setattr(
        module.importlib.util,
        "find_spec",
        lambda _name: SimpleNamespace(origin=str(package / "__init__.py")),
    )
    value = module._artifact("test-distribution", "installed_package")
    assert value["lock_sha256"] == marker["lock_sha256"]
    before = value["code_sha256"]
    (package / "__init__.py").write_text("# altered installed code\n")
    assert module._artifact("test-distribution", "installed_package")["code_sha256"] != before
    (package / "_recovery_build.json").unlink()
    with pytest.raises(InvocationStateError):
        module._artifact("test-distribution", "installed_package")


def test_same_sdk_version_with_changed_installed_serializer_code_changes_profile_digest(
    tmp_path, monkeypatch
):
    import open_workflow_agent.recovery_definitions as module

    source = tmp_path / "serializer.py"
    source.write_text("# serializer version one\n")
    installed = SimpleNamespace(
        version="1.0.0", files=[source.name], locate_file=lambda name: tmp_path / name
    )
    monkeypatch.setattr(module.importlib.metadata, "distribution", lambda _name: installed)
    original = module._sdk_digest("test-checkpoint-sdk")
    source.write_text("# changed serialization implementation, same package version\n")
    assert module._sdk_digest("test-checkpoint-sdk") != original
    source.unlink()
    with pytest.raises(InvocationStateError):
        module._sdk_digest("test-checkpoint-sdk")


def test_private_execution_plan_cannot_differ_from_archived_public_inputs(archive_tree):
    _, archive, parent, plan, _ = archive_tree
    before = _rows(archive_tree)
    derived = plan.execution_source
    derived["do"] = [{"hidden": {"set": {"different": True}}}]
    with pytest.raises(InvocationStateError) as caught:
        archive.admit(
            replace(plan, execution_json=json.dumps(derived)),
            parent,
            None,
            execution_lease=parent._execution_lease,
        )
    assert caught.value.details["reason"] == "archive_invalid"
    assert _rows(archive_tree) == before


def test_declared_deployment_binding_never_qualifies_changed_physical_sdk(tmp_path, monkeypatch):
    import open_workflow_agent.services as service_module

    physical = {
        "profile": PROFILE,
        "artifacts": [{"code_sha256": "a" * 64}],
        "sdk_code": {"checkpoint-sdk": "b" * 64},
        "continuation_supported": False,
    }

    def installed(engine, *, backend, config):
        return {**copy.deepcopy(physical), "engine": engine, "backend": backend}

    monkeypatch.setattr(service_module, "recovery_profile", installed)
    declaration = {"id": "qualified-deployment", "version": "1.0.0"}
    services = RuntimeServices(
        RuntimeConfig.model_validate({"model": {"options": {"private_option": "not-archived"}}}),
        model=FakeModel(),
        database_root=tmp_path,
        recovery_profile_provider=lambda _engine: dict(declaration),
    )
    parent = None
    try:
        plan = compile_workflow(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "test",
                    "name": "sdk-profile",
                    "version": "1.0.0",
                },
                "do": [{"finish": {"set": {"original": True}}}],
            }
        )
        parent = services.invocations.create(
            engine="reference",
            session_id=None,
            user_id=None,
            owner_principal="owner",
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        parent._execution_lease = services.execution_ownership.acquire(parent, seconds=300)
        archive = services.recovery_definitions
        archive.admit(plan, parent, None, execution_lease=parent._execution_lease)
        assert archive.resolve(parent).fingerprint == plan.fingerprint
        before = _rows((services, archive, parent, plan, {}))
        original = services._recovery_profile("reference")
        physical["sdk_code"]["checkpoint-sdk"] = "c" * 64
        current = services._recovery_profile("reference")
        assert current["deployment_binding"] == original["deployment_binding"]
        assert current["sdk_code"] != original["sdk_code"]
        with pytest.raises(InvocationStateError) as caught:
            archive.resolve(parent)
        assert caught.value.details["reason"] == "runtime_profile_changed"
        assert _rows((services, archive, parent, plan, {})) == before
        assert services.model.calls == []
    finally:
        if parent is not None:
            services.execution_ownership.release(parent._execution_lease)
        services.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("field,value", [("max_depth", 64), ("max_children", 100_000)])
def test_changed_child_quota_cannot_reinterpret_original_deployment(
    tmp_path, backend, field, value
):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    handle = None
    try:
        plan = compile_workflow(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "test",
                    "name": "original-child-policy-" + uuid4().hex,
                    "version": "1.0.0",
                },
                "do": [{"pause": {"wait": {"seconds": 60}}}],
            }
        )
        handle = services.invocations.create(
            engine="reference",
            session_id=None,
            user_id=None,
            owner_principal="owner",
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        handle._execution_lease = services.execution_ownership.acquire(handle, seconds=300)
        archive = services.recovery_definitions
        archive.admit(plan, handle, {"original": True}, execution_lease=handle._execution_lease)
        assert archive.resolve(handle).fingerprint == plan.fingerprint
        before = _rows((services, archive, handle, plan, {}))
        original = services.invocations.get(handle.invocation_id)._persisted_payload
        setattr(services.config.child_workflows, field, value)
        with pytest.raises(InvocationStateError) as caught:
            archive.resolve(handle)
        assert caught.value.details["reason"] == "runtime_profile_changed"
        assert _rows((services, archive, handle, plan, {})) == before
        assert services.invocations.get(handle.invocation_id)._persisted_payload == original
        assert services.model.calls == []
    finally:
        if handle is not None:
            services.execution_ownership.release(handle._execution_lease)
        services.close()


def _root_editable_fallback(tmp_path, monkeypatch, engine):
    import open_workflow_agent.recovery_definitions as module

    project = tmp_path / "explicitly-installed-root"
    core = project / "core" / "src" / "open_workflow_agent"
    adapter_name, package = module._ADAPTERS[engine]
    engine_project = project / "engines" / engine
    adapter = engine_project / "src" / package
    for directory in (core, adapter):
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text("# exact installed development source\n")
    (project / "uv.lock").write_text("version = 1\n")
    (project / "pyproject.toml").write_text('[project]\nname="open-workflow-agent"\nversion="1"\n')
    (engine_project / "uv.lock").write_text("version = 1\n# adapter's independent lock\n")
    (engine_project / "pyproject.toml").write_text(
        f'[project]\nname="{adapter_name}"\nversion="0.2.0"\n'
    )
    state = {"editable": True, "sdk_present": False, "origin": adapter / "__init__.py"}
    installed = SimpleNamespace(
        version="0.2.0",
        locate_file=lambda name: project / name,
        read_text=lambda _name: json.dumps(
            {"url": project.as_uri(), "dir_info": {"editable": state["editable"]}}
        ),
    )

    def distribution(name):
        root_name = "open-workflow-agent-core" if state.get("standalone") else "open-workflow-agent"
        if name == root_name:
            return installed
        if state["sdk_present"] and name == module._NATIVE_SDKS[engine]:
            return SimpleNamespace(version="1.0.0")
        raise module.importlib.metadata.PackageNotFoundError(name)

    def version(name):
        raise module.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(module.importlib.metadata, "distribution", distribution)
    monkeypatch.setattr(module.importlib.metadata, "version", version)
    monkeypatch.setattr(
        module.importlib.util,
        "find_spec",
        lambda name: SimpleNamespace(
            origin=str(core / "__init__.py" if name == "open_workflow_agent" else state["origin"])
        ),
    )
    return module, state, engine_project


@pytest.mark.parametrize("engine", ["adk", "langgraph"])
def test_root_editable_source_fallback_has_exact_independent_artifact_binding(
    tmp_path, monkeypatch, engine
):
    module, _, engine_project = _root_editable_fallback(tmp_path, monkeypatch, engine)
    original = module.recovery_profile(engine, backend="sqlite")
    assert original["adapter_mode"] == "common_fallback"
    assert original["native_binding"] == "common-fallback-v1"
    adapter = original["artifacts"][1]
    assert adapter["provenance"] == "owa/root-editable-adapter-source/v1"
    assert adapter["root_distribution"] == "open-workflow-agent"
    assert original["dependencies"][module._NATIVE_SDKS[engine]] is None
    assert module.recovery_profile(engine, backend="sqlite") == original
    (engine_project / "uv.lock").write_text("version = 1\n# changed locked dependency\n")
    changed = module.recovery_profile(engine, backend="sqlite")
    assert changed["artifacts"][1]["lock_sha256"] != adapter["lock_sha256"]
    package = module._ADAPTERS[engine][1]
    (engine_project / "src" / package / "__init__.py").write_text("# changed fallback semantics\n")
    assert (
        module.recovery_profile(engine, backend="sqlite")["artifacts"][1]["code_sha256"]
        != adapter["code_sha256"]
    )


@pytest.mark.parametrize("engine", ["adk", "langgraph"])
@pytest.mark.parametrize(
    "damage", ["production", "sdk_present", "foreign_checkout", "metadata", "standalone"]
)
def test_source_adapter_provenance_cannot_be_inferred_for_production_or_native_execution(
    tmp_path, monkeypatch, engine, damage
):
    module, state, engine_project = _root_editable_fallback(tmp_path, monkeypatch, engine)
    if damage == "production":
        state["editable"] = False
        # A valid installed root wheel marker does not permit finding its
        # missing optional adapter in a neighboring source checkout.
        marker = engine_project.parents[1] / "open_workflow_agent" / "_recovery_build.json"
        marker.parent.mkdir()
        marker.write_text(
            json.dumps(
                {
                    "profile": "owa/recovery-build/v1",
                    "project": "open-workflow-agent",
                    "version": "0.2.0",
                    "lock_sha256": "e" * 64,
                    "metadata_sha256": "f" * 64,
                }
            )
        )
    elif damage == "sdk_present":
        state["sdk_present"] = True
    elif damage == "foreign_checkout":
        foreign = tmp_path / "neighboring-checkout" / "__init__.py"
        foreign.parent.mkdir()
        foreign.write_text("# source from an unrelated checkout\n")
        state["origin"] = foreign
    elif damage == "standalone":
        state["standalone"] = True
    else:
        (engine_project / "pyproject.toml").write_text(
            '[project]\nname="different-project"\nversion="0.2.0"\n'
        )
    with pytest.raises(InvocationStateError) as caught:
        module.recovery_profile(engine, backend="sqlite")
    assert caught.value.details["reason"] == "runtime_profile_unavailable"


@pytest.fixture(params=["sqlite", "postgresql"])
def role_archive_tree(tmp_path, request):
    """Actual leased common admission; the manifest is explicitly a unit profile."""
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and not datasource:
        pytest.skip("actual PostgreSQL datasource required")
    config = RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource},
            "agent": {"roles": {"assistant": {"id": "assistant", "version": "1.0.0"}}},
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    assert (
        services.invocations.connection.kind
        == services.effect_journal.connection.kind
        == request.param
    )
    manifest = {
        "profile": PROFILE,
        "backend": request.param,
        "artifact": "a" * 64,
        "sdk": "test-only/common-role-admission",
        "continuation_supported": True,
        "policy": {"agent_roles": services.role_bindings.snapshot()},
    }
    archive = RecoveryDefinitionArchive(
        services.effect_journal,
        services.invocations,
        profile_provider=lambda engine: {**copy.deepcopy(manifest), "engine": engine},
        trust_verifier=services._verify_archived_definition_trust,
    )
    lease = None

    def prepare(reference):
        nonlocal lease
        plan = compile_workflow(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "role-archive",
                    "name": uuid4().hex,
                    "version": "1.0.0",
                },
                "do": [{"respond": {"call": reference, "with": {"input": "${ . }"}}}],
            },
            enabled_function_calls=services.role_function_calls | {reference},
        )
        handle = services.invocations.create(
            engine="reference",
            session_id=None,
            user_id="correlation",
            owner_principal=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        lease = services.execution_ownership.acquire(handle, seconds=60)
        handle._execution_lease = lease
        return plan, handle

    try:
        yield services, archive, manifest, prepare
    finally:
        if lease is not None:
            services.execution_ownership.release(lease)
        services.close()


@pytest.mark.parametrize("kind", ["agent", "llm"])
def test_initial_named_role_archive_roundtrip_uses_exact_current_manifest_bindings(
    role_archive_tree, kind
):
    services, archive, _, prepare = role_archive_tree
    reference = kind + "-assistant:1.0.0@default"
    plan, handle = prepare(reference)
    original = {"original": None, "value": False}
    archive.admit(plan, handle, original, execution_lease=handle._execution_lease)
    restored = archive.resolve(handle)
    assert restored == plan and restored.source == plan.source
    assert restored.tasks[0].reference == "/do/0/respond"
    assert services.invocations.read_input(handle) == original
    assert archive.has_binding(handle)
    receipt = services.effect_journal.get(admission_identity(handle))
    assert receipt is not None and receipt.status == "completed"
    assert services.model.calls == []
    assert all(
        getattr(model, "calls", []) == [] for model in services.role_bindings._models.values()
    )


@pytest.mark.parametrize("damage", ["missing", "version", "name", "shape", "prefix"])
def test_initial_role_archive_cannot_grant_a_missing_or_foreign_binding(role_archive_tree, damage):
    services, archive, manifest, prepare = role_archive_tree
    reference = "agent-assistant:1.0.0@default"
    if damage == "prefix":
        reference = "agent-assistant-extra:1.0.0@default"
    plan, handle = prepare(reference)
    if damage == "missing":
        manifest["policy"].pop("agent_roles")
    elif damage == "version":
        manifest["policy"]["agent_roles"]["bindings"][0]["version"] = "2.0.0"
    elif damage == "name":
        manifest["policy"]["agent_roles"]["bindings"][0]["name"] = "different"
    elif damage == "shape":
        manifest["policy"]["agent_roles"].pop("effective_tool_bindings")
    tree = (services, archive, handle, plan, manifest)
    before = _rows(tree)
    with pytest.raises(InvocationStateError):
        archive.admit(plan, handle, {"original": None}, execution_lease=handle._execution_lease)
    assert _rows(tree) == before
    assert not archive.has_binding(handle)
    assert services.model.calls == []


@pytest.mark.parametrize("changed_policy", ["catalog", "schema"])
@pytest.mark.asyncio
async def test_composed_resolver_trust_accepts_captured_inputs_and_rejects_changes_before_lease(
    archive_tree, monkeypatch, changed_policy
):
    import open_workflow_agent.services as service_module

    services, _, _, _, _ = archive_tree
    monkeypatch.setattr(
        service_module,
        "recovery_profile",
        lambda engine, *, backend, config: {
            "profile": PROFILE,
            "engine": engine,
            "backend": backend,
            "artifacts": [{"code_sha256": "a" * 64}],
            "continuation_supported": True,
        },
    )
    schema_uri = "https://schema.test/original"
    schema_bytes = b'{"type":"object"}'
    fetched = []

    def remote(request):
        fetched.append(str(request.url))
        if request.url.host == "schema.test":
            return httpx.Response(200, content=schema_bytes)
        assert request.url.host == "catalog.test"
        return httpx.Response(
            200,
            json={"call": "http", "with": {"method": "GET", "endpoint": "https://api.test/result"}},
        )

    resolver = ExternalCatalogResolver(
        {"trusted": ExternalCatalogConfig(allowed_hosts=["catalog.test", "api.test"])},
        resource_policies={
            "schema": ExternalResourceConfig(
                allowed_hosts=["schema.test"],
                allowed_endpoints=[schema_uri],
                integrity_pins={schema_uri: hashlib.sha256(schema_bytes).hexdigest()},
            )
        },
        http=HttpClient(transport=httpx.MockTransport(remote)),
    )
    services.external_catalogs = resolver
    assert services.config.workflow.external_catalogs == {}
    assert services.config.workflow.external_resources == {}
    source = generate_default_workflow()
    source["use"] = {"catalogs": {"trusted": {"endpoint": "https://catalog.test/root"}}}
    source["input"] = {"schema": {"format": "json", "resource": {"endpoint": schema_uri}}}
    source["do"] = [{"remote": {"call": "echo:1.0.0@trusted"}}]
    plan = await resolve_and_compile_workflow(
        source, trusted_catalogs=resolver.policies, resolver=resolver, catalog=services.catalog
    )
    assert len(plan.imports) == len(plan.resources) == 1 and len(fetched) == 2
    # Admission and recovery retain a named credential reference without ever
    # resolving that credential or fetching the captured definition again.
    resolver.policies["trusted"].authentication.security_profile = "catalog-reader"
    handle = services.invocations.create(
        engine="reference",
        session_id=None,
        user_id="correlation",
        owner_principal="owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle, seconds=300)
    handle._execution_lease = lease
    archive = services.recovery_definitions
    try:
        archive.admit(plan, handle, {"original": True}, execution_lease=lease)
        assert archive.resolve(handle).fingerprint == plan.fingerprint
        profile = services._recovery_profile("reference")
        assert profile["policy"]["catalogs"]["trusted"]["authentication"] == {
            "security_profile": "catalog-reader"
        }
        assert profile["policy"]["schemas"]["schema"]["integrity_pins"] == {
            schema_uri: hashlib.sha256(schema_bytes).hexdigest()
        }
        services.invocations.update(handle, status="waiting")
    finally:
        services.execution_ownership.release(lease)
        handle._execution_lease = None
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    try:
        before = _rows((services, archive, handle, plan, {}))
        state = services.invocations.get(handle.invocation_id)._persisted_payload
        events = list(services.events.events)
        if changed_policy == "catalog":
            resolver.policies["trusted"].allowed_hosts = ["different.test"]
        else:
            resolver.schemas.policies["schema"].integrity_pins = {schema_uri: "f" * 64}
        with pytest.raises(InvocationStateError) as caught:
            await engine.resume(handle, None, plan)
        assert caught.value.details["reason"] == "runtime_profile_changed"
        assert _rows((services, archive, handle, plan, {})) == before
        assert services.invocations.get(handle.invocation_id)._persisted_payload == state
        assert services.events.events == events
        assert len(fetched) == 2 and services.model.calls == []
        assert handle._execution_lease is None and engine._active == {}
    finally:
        await engine.shutdown()


@pytest.mark.parametrize("oversized", [False, True])
def test_installed_source_read_is_file_sized_and_rejects_oversize_before_read(
    tmp_path, monkeypatch, oversized
):
    from pathlib import Path

    import open_workflow_agent.recovery_definitions as module

    path = tmp_path / "installed.py"
    path.write_bytes(b"small")
    original_open = Path.open
    reads = []

    class ObservedFile:
        def __init__(self, source):
            self.source = source

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.source.close()

        def fileno(self):
            return self.source.fileno()

        def read(self, size):
            reads.append(size)
            return self.source.read(size)

    def observed_open(candidate, mode="r", *args, **kwargs):
        source = original_open(candidate, mode, *args, **kwargs)
        return ObservedFile(source) if candidate == path and mode == "rb" else source

    monkeypatch.setattr(Path, "open", observed_open)
    if oversized:
        with pytest.raises(InvocationStateError) as caught:
            module._file_bytes(path, 4)
        assert caught.value.details["reason"] == "runtime_profile_unavailable"
        assert reads == []
    else:
        assert module._file_bytes(path, 128_000_000) == b"small"
        assert reads == [6]
        path.write_bytes(b"fresh")
        assert module._file_bytes(path, 128_000_000) == b"fresh"
        assert reads == [6, 6]


@pytest.mark.parametrize("change", ["growth", "truncation", "metadata"])
def test_installed_source_change_during_read_fails_closed(tmp_path, monkeypatch, change):
    from pathlib import Path

    import open_workflow_agent.recovery_definitions as module

    path = tmp_path / "installed.py"
    path.write_bytes(b"original")
    original_open = Path.open

    class ChangingFile:
        def __init__(self, source):
            self.source = source

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.source.close()

        def fileno(self):
            return self.source.fileno()

        def read(self, size):
            assert size == len(b"original") + 1
            if change == "growth":
                path.write_bytes(b"original-and-new")
            elif change == "truncation":
                path.write_bytes(b"cut")
            value = self.source.read(size)
            if change == "metadata":
                current = path.stat()
                os.utime(path, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000_000))
            return value

    def changing_open(candidate, mode="r", *args, **kwargs):
        source = original_open(candidate, mode, *args, **kwargs)
        return ChangingFile(source) if candidate == path and mode == "rb" else source

    monkeypatch.setattr(Path, "open", changing_open)
    with pytest.raises(InvocationStateError) as caught:
        module._file_bytes(path, 128_000_000)
    assert caught.value.details["reason"] == "runtime_profile_unavailable"
