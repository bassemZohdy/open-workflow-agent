"""Exact common authority companions admitted by ADK's private receipt reader."""

import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import ReceiptDependency
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.execution_authority import ExecutionAuthority, authority_scope
from open_workflow_agent.execution_authorization import authority_identity
from open_workflow_agent.recovery_definitions import PROFILE
from open_workflow_agent.security import Principal
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_adk.native import _RECEIPT_AUTHOR, _ReceiptEvents


@pytest.mark.parametrize(
    "corruption",
    [None, "binding-missing", "receipt-missing", "hash", "occurrence", "scope", "foreign"],
)
def test_root_authority_companion_requires_exact_independent_evidence(tmp_path, corruption):
    config = RuntimeConfig.model_validate(
        {
            "security": {
                "profiles": {
                    "ingress": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_ADK_AUTHORITY_UNUSED"},
                        "principal": "alice",
                    }
                }
            },
            "execution_authorization": {
                "enabled": True,
                "id": "adk-restore",
                "policy": {"rules": []},
            },
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    # This fixture verifies the native receipt reader against real common
    # proofs; it does not claim an SDK checkpoint or a physical deployment.
    services.recovery_definitions.profile_provider = lambda engine: {
        "profile": PROFILE,
        "engine": engine,
        "backend": "sqlite",
        "continuation_supported": True,
        "qualification": "test-only/adk-authority-reader",
    }
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "tests",
                "name": "adk-authority-reader",
                "version": "1.0.0",
            },
            "do": [{"leaf": {"set": {"done": True}}}],
        }
    )
    handle = services.invocations.create(
        engine="adk",
        session_id="original-session",
        user_id="untrusted-label",
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle, seconds=60)
    handle._execution_lease = lease
    try:
        with authority_scope(ExecutionAuthority(Principal("alice"), "ingress")):
            services.recovery_definitions.admit(
                plan, handle, {"original": True}, execution_lease=lease
            )
        identity = authority_identity(handle)
        receipt = services.effect_journal.get(identity)
        assert receipt is not None and receipt.status == "completed"
        dependency = ReceiptDependency.capture(identity, receipt)
        engine = AdkWorkflowEngine()
        engine.services = services
        context = engine._recovery_context(plan, handle, {})
        assert context.authority_admission == identity
        assert identity.task_reference not in context.task_references
        connection = services.effect_journal.connection
        if corruption == "binding-missing":
            connection.execute(
                "DELETE FROM execution_authorities WHERE invocation_id=?", (handle.invocation_id,)
            )
        elif corruption == "receipt-missing":
            connection.execute("DELETE FROM workflow_effects WHERE identity=?", (identity.key,))
        elif corruption == "hash":
            dependency = replace(dependency, receipt_digest="0" * 64)
        elif corruption == "occurrence":
            dependency = replace(dependency, identity=replace(identity, occurrence=(("/do", 2),)))
        elif corruption == "scope":
            dependency = replace(dependency, identity=replace(identity, task_reference="/other"))
        elif corruption == "foreign":
            dependency = replace(dependency, identity=replace(identity, invocation_id="other"))
        connection.commit()
        # Native SDK storage round-trips event metadata through JSON. Every
        # corruption below must pass the transport decoder before testing its
        # intended common proof or exact native scope rejection.
        serialized_dependency = json.loads(json.dumps(asdict(dependency), allow_nan=False))
        assert ReceiptDependency.from_value(serialized_dependency) == dependency
        if corruption is None:
            with pytest.raises(ValueError, match="workflow receipt occurrence is invalid"):
                ReceiptDependency.from_value(asdict(dependency))
        session = SimpleNamespace(
            events=[
                SimpleNamespace(
                    invocation_id=handle.engine_execution_reference,
                    author=_RECEIPT_AUTHOR,
                    content=None,
                    custom_metadata={
                        "version": 1,
                        "invocation_id": handle.invocation_id,
                        "workflow_fingerprint": plan.fingerprint,
                        "sequence": 1,
                        "phase": "receipt",
                        "receipts": [serialized_dependency],
                    },
                )
            ]
        )
        before = tuple(connection.raw.iterdump())
        reader = _ReceiptEvents(context)
        if corruption is None:
            reader.restore(session, handle.engine_execution_reference)
            assert reader.dependencies == {identity.key: dependency}
        else:
            if corruption in {"occurrence", "scope", "foreign"}:
                expected = ValueError
                message = "native receipt dependency identity is invalid"
            else:
                expected = WorkflowExecutionError
                message = (
                    "workflow effect receipt could not be read"
                    if corruption == "binding-missing"
                    else "workflow action receipt dependency is unavailable or inconsistent"
                )
            with pytest.raises(expected, match=message):
                reader.restore(session, handle.engine_execution_reference)
        assert tuple(connection.raw.iterdump()) == before
        assert services.model.calls == []
        assert services.invocations.read_input(handle) == {"original": True}
    finally:
        services.execution_ownership.release(lease)
        services.close()
