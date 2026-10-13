"""Original definition admission retains finite inputs and rejects reinterpretation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency, effect_request_digest
from open_workflow_agent.errors import (
    InvocationStateConflict,
    InvocationStateError,
    WorkflowDefinitionChanged,
    WorkflowExecutionError,
)
from open_workflow_agent.external_catalog import ResolvedCatalogFunction
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
)


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
                "name": "retained",
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
    assert "private resolver" not in json.dumps(snapshot)
    assert _rows(archive_tree) == before


def test_default_profile_never_hashes_or_retains_unclassified_secret_options(monkeypatch):
    import open_workflow_agent.recovery_definitions as module

    monkeypatch.setattr(module, "_artifact", lambda *args: {"code_sha256": "d" * 64})
    secret = "test-secret-value-that-must-not-enter-a-manifest"
    config = RuntimeConfig.model_validate({"model": {"options": {"api_key": secret}}})
    value = module.recovery_profile("reference", backend="sqlite", config=config)
    encoded = json.dumps(value)
    assert not value["continuation_supported"]
    assert secret not in encoded and hashlib.sha256(secret.encode()).hexdigest() not in encoded
    old = module.recovery_profile("reference", backend="sqlite", config=RuntimeConfig())
    config = RuntimeConfig.model_validate(
        {"workflow": {"definition": {"deployment": "v2"}}, "observability": {"log_level": "DEBUG"}}
    )
    assert module.recovery_profile("reference", backend="sqlite", config=config) == old


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
