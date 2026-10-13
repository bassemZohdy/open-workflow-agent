"""Actual native OpenAPI producer stopped only by its owning test process."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from _process_recovery_worker import write_json
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.openapi_admission import OPERATION_POLICY
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import compile_workflow
from test_native_checkpoint_fencing import _snapshot as native_snapshot

CA_ENV, CERT_ENV, KEY_ENV = "OPENAPI_STOP_CA", "OPENAPI_STOP_CERT", "OPENAPI_STOP_KEY"
METADATA_CA_ENV = "OPENAPI_STOP_METADATA_CA"


async def run(directory: Path, mode: str):
    scenario = json.loads((directory / "scenario.json").read_text())
    saved = None if mode == "start" else json.loads((directory / "ready.json").read_text())
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if scenario["backend"] == "postgresql" else None
    config = RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource},
            "security": {
                "profiles": {
                    "receiver": {
                        "type": "mtls",
                        "certificate": {"from_env": CERT_ENV},
                        "private_key": {"from_env": KEY_ENV},
                        "ca_bundle": {"from_env": CA_ENV},
                    }
                }
            },
            "protocols": {
                "security_profile": "receiver",
                "openapi_credential_endpoints": {"receiver": [scenario["endpoint"]]},
            },
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else directory / "runtime"
    )
    # Public description retrieval uses this independently selected CA-only
    # transport trust. It must not inherit the named business mTLS identity.
    metadata_ca = os.environ[METADATA_CA_ENV]
    assert metadata_ca == os.environ[CA_ENV] and Path(metadata_ca).is_file()
    services.protocols.http.verify_tls = metadata_ca
    assert services.protocols.http.client_cert is None
    assert getattr(services.protocols.http.authentication, "profile_name", None) == "receiver"
    assert services.protocols._openapi_credential_endpoints == {"receiver": (scenario["endpoint"],)}
    assert services.invocations.connection.kind == scenario["backend"]
    patches = pytest.MonkeyPatch()

    async def no_background(_dispatcher):
        return None

    patches.setattr(TimerDispatcher, "start", no_background)
    module, name = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[scenario["engine"]]
    engine = getattr(importlib.import_module(module), name)()
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "openapi-stop",
                "name": "original-operation",
                "version": "1.0.0",
            },
            "do": [
                {
                    "action": {
                        "call": "openapi",
                        "with": {
                            "document": {"endpoint": scenario["document_uri"]},
                            "operationId": "commit",
                            "parameters": {
                                "case": "${ $workflow.input.case }",
                                "value": "${ $workflow.input.original }",
                            },
                        },
                    }
                }
            ],
        }
    )
    original = {"case": scenario["subject"], "original": "original-value"}
    parent = identity = policy = None

    def evidence():
        stored_policy = services.effect_journal.get(policy)
        business = services.effect_journal.get(identity)
        assert stored_policy is not None and stored_policy.status == "completed"
        ownership = services.execution_ownership.connection.execute(
            "SELECT owner,generation,expires_at FROM invocation_ownership WHERE invocation_id=?",
            (parent.invocation_id,),
        ).fetchone()
        native_rows = {
            table: len(rows)
            for table, rows in native_snapshot(services, parent, scenario["backend"]).items()
        }
        assert native_rows.get("sessions" if scenario["engine"] == "adk" else "checkpoints", 0) > 0
        return {
            "pid": os.getpid(),
            "invocation_id": parent.invocation_id,
            "session_id": parent.session_id,
            "identity": asdict(identity),
            "policy": asdict(policy),
            "fingerprint": plan.fingerprint,
            "original": services.invocations.read_input(parent),
            "principal": parent.owner_principal,
            "native_available": True,
            "native_rows": native_rows,
            "backend": services.invocations.connection.kind,
            "policy_proof": ReceiptDependency.capture(policy, stored_policy).receipt_digest,
            "request_digest": stored_policy.output["request_digest"],
            "deadline_at": stored_policy.output["deadline_at"],
            "receipt_status": business.status if business else None,
            "business_owner": business.owner if business else None,
            "operation_id": business.operation_id
            if business
            else f"{parent.invocation_id}:/do/0/action",
            "business_proof": ReceiptDependency.capture(identity, business).receipt_digest
            if business is not None and business.status != "pending"
            else None,
            "generation": ownership[1],
        }

    try:
        await engine.initialize(services)
        engine._execution_ownership_seconds = 10
        assert (
            engine.native.available
            if scenario["engine"] == "adk"
            else engine.native.task_nodes_available
        )
        notify = engine.executor._notify_receipt

        async def receipt_boundary(state, observed, receipt):
            nonlocal identity, policy
            await notify(state, observed, receipt)
            if observed.operation == OPERATION_POLICY:
                policy, identity = observed, replace(observed, operation="call:openapi")
                if mode == "start" and scenario["boundary"] == "before-dispatch":
                    write_json(directory / "ready.json", evidence())
                    await asyncio.Event().wait()
            if (
                mode == "start"
                and observed.operation == "call:openapi"
                and scenario["boundary"] == "receipt"
            ):
                identity = observed
                write_json(directory / "ready.json", evidence())
                await asyncio.Event().wait()

        engine.executor._notify_receipt = receipt_boundary
        begin = services.effect_journal.begin

        def begin_boundary(observed, digest, owner, **kwargs):
            nonlocal identity
            receipt = begin(observed, digest, owner, **kwargs)
            if (
                mode == "start"
                and observed.operation == "call:openapi"
                and scenario["boundary"] == "business-commit"
            ):
                identity = observed
                write_json(directory / "ready.json", evidence())
            return receipt

        services.effect_journal.begin = begin_boundary
        if mode == "start":
            parent = services.invocations.create(
                engine=scenario["engine"],
                session_id="original-session",
                user_id="correlation-user",
                owner_principal="original-owner",
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            result = await asyncio.wait_for(engine.invoke(plan, parent, original), 100)
            assert scenario["boundary"] == "terminal" and result.status == (
                "faulted" if scenario["outcome"] == "fault" else "completed"
            ), result.error
            write_json(directory / "ready.json", evidence())
            await asyncio.Event().wait()
        assert saved is not None
        identity = EffectIdentity.from_value(saved["identity"])
        policy = EffectIdentity.from_value(saved["policy"])
        parent = services.invocations.get(saved["invocation_id"])
        assert parent is not None and services.invocations.read_input(parent) == original
        assert plan.fingerprint == saved["fingerprint"]
        assert (
            ReceiptDependency.capture(policy, services.effect_journal.get(policy)).receipt_digest
            == saved["policy_proof"]
        )
        async with asyncio.timeout(90):
            while True:
                parent = services.invocations.get(saved["invocation_id"])
                try:
                    result = await engine.resume(
                        parent,
                        {"untrusted": "not-original-input"},
                        services.resolve_original_definition(parent),
                    )
                    break
                except InvocationStateConflict:
                    await asyncio.sleep(0.1)
        expected = (
            "faulted"
            if scenario["boundary"] == "business-commit" or scenario["outcome"] == "fault"
            else "completed"
        )
        assert result.status == expected, result.error
        business = services.effect_journal.get(identity)
        assert business is not None and business.request_digest == saved["request_digest"]
        assert business.operation_id == saved["operation_id"]
        assert (business.status == "pending") == (scenario["boundary"] == "business-commit")
        if scenario["boundary"] != "before-dispatch":
            assert business.owner == saved["business_owner"]
        if saved["business_proof"] is not None:
            assert (
                ReceiptDependency.capture(identity, business).receipt_digest
                == saved["business_proof"]
            )
        duplicate = await engine.resume(parent, {}, plan)
        final = evidence()
        final.update(
            status=result.status,
            output=result.output,
            error=result.error,
            duplicate=duplicate.as_dict(),
            model_calls=services.model.calls,
        )
        write_json(directory / "result.json", final)
    finally:
        await engine.shutdown()
        services.close()
        patches.undo()
        write_json(directory / (mode + "-cleaned.json"), {"pid": os.getpid()})


if __name__ == "__main__":
    directory, mode = Path(sys.argv[1]), sys.argv[2]
    try:
        asyncio.run(run(directory, mode))
    except BaseException as error:
        write_json(
            directory / (mode + "-failure.json"),
            {"type": type(error).__name__, "code": getattr(error, "code", None)},
        )
        raise
