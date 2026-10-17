"""Actual native HTTP producer/recovery controlled by a process-stop fixture."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from _external_receiver import PRINCIPAL, RECEIVER_ID, TOKEN_ENV
from _process_recovery_worker import write_json
from _recovery_failure_artifact import failure_artifact
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.external_recovery import (
    ADMISSION_OPERATION,
    HttpExternalActionRecovery,
    TrustedHttpReceiver,
)
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import compile_workflow


async def run(directory: Path, mode: str) -> None:
    scenario = json.loads((directory / "scenario.json").read_text())
    saved = None if mode == "start" else json.loads((directory / "ready.json").read_text())
    patches = pytest.MonkeyPatch()

    async def no_background(_dispatcher: TimerDispatcher) -> None:
        return None

    patches.setattr(TimerDispatcher, "start", no_background)
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if scenario["backend"] == "postgresql" else None
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else directory / "runtime",
    )
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "receiver": {
                    "type": "bearer",
                    "principal": PRINCIPAL,
                    "token": {"from_env": TOKEN_ENV},
                }
            }
        }
    )
    http = HttpClient(
        timeout=60,
        max_response_bytes=65536,
        ca_bundle=scenario["certificate"],
        allowed_hosts={"127.0.0.1"},
        authentication=ProfileAuthentication(security, "receiver"),
    )
    services.protocols = ProtocolServices(http)
    services.tools.protocols = services.protocols
    services.external_action_recovery = HttpExternalActionRecovery(
        services.effect_journal,
        services.invocations,
        http,
        (
            TrustedHttpReceiver(
                RECEIVER_ID,
                scenario["business_endpoint"],
                scenario["lookup_endpoint"],
                "receiver",
                PRINCIPAL,
            ),
        ),
    )
    module, class_name = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[scenario["engine"]]
    engine = getattr(importlib.import_module(module), class_name)()
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "external-recovery",
                "name": "native-producer",
                "version": "1.0.0",
            },
            "do": [
                {
                    "action": {
                        "call": "http",
                        "with": {
                            "method": "POST",
                            "endpoint": scenario["business_endpoint"],
                            "body": "${ $workflow.input }",
                        },
                    }
                }
            ],
        }
    )
    original = {"case": scenario["subject"], "original": "preserved"}
    parent: Any = None
    action_identity: EffectIdentity | None = None

    def ready(identity: EffectIdentity) -> None:
        receipt = services.effect_journal.get(identity)
        assert receipt is not None
        write_json(
            directory / "ready.json",
            {
                "pid": os.getpid(),
                "invocation_id": parent.invocation_id,
                "native_available": True,
                "identity": asdict(identity),
                "operation_id": receipt.operation_id,
                "owner": receipt.owner,
                "request_digest": receipt.request_digest,
                "fingerprint": plan.fingerprint,
                "original": original,
                "receipt_status": receipt.status,
            },
        )

    try:
        await engine.initialize(services)
        engine._execution_ownership_seconds = 10
        assert (
            engine.native.task_nodes_available
            if scenario["engine"] == "langgraph"
            else engine.native.available
        )
        notify = engine.executor._notify_receipt

        async def receipt_boundary(state: Any, identity: EffectIdentity, receipt: Any) -> None:
            nonlocal action_identity
            await notify(state, identity, receipt)
            if identity.operation == ADMISSION_OPERATION:
                action_identity = EffectIdentity(
                    identity.invocation_id,
                    identity.workflow_fingerprint,
                    identity.task_reference,
                    identity.occurrence,
                    "call:http",
                )
                if mode == "start" and scenario["boundary"] in {
                    "before-dispatch",
                    "business-commit",
                }:
                    ready(action_identity)
                    if scenario["boundary"] == "before-dispatch":
                        await asyncio.Event().wait()
            if (
                mode == "start"
                and identity.operation == "call:http"
                and scenario["boundary"] == "receipt"
            ):
                action_identity = identity
                ready(identity)
                await asyncio.Event().wait()

        engine.executor._notify_receipt = receipt_boundary
        if mode == "start":
            parent = services.invocations.create(
                engine=scenario["engine"],
                session_id=None,
                user_id="correlation",
                owner_principal="caller",
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            result = await asyncio.wait_for(engine.invoke(plan, parent, original), timeout=100)
            assert scenario["boundary"] == "terminal", result.error
            assert result.status == ("faulted" if scenario["outcome"] == "fault" else "completed")
            assert action_identity is not None
            ready(action_identity)
            await asyncio.Event().wait()
        assert saved is not None
        identity = EffectIdentity.from_value(saved["identity"])
        assert identity.workflow_fingerprint == plan.fingerprint == saved["fingerprint"]
        parent = services.invocations.get(saved["invocation_id"])
        assert parent is not None and services.invocations.read_input(parent) == original
        before = services.effect_journal.get(identity)
        assert before is not None
        assert (before.owner, before.operation_id, before.request_digest) == (
            saved["owner"],
            saved["operation_id"],
            saved["request_digest"],
        )
        async with asyncio.timeout(90):
            while True:
                parent = services.invocations.get(saved["invocation_id"])
                try:
                    result = await engine.resume(parent, {"untrusted": "resume-is-not-input"}, plan)
                    break
                except InvocationStateConflict:
                    await asyncio.sleep(0.1)
        receipt = services.effect_journal.get(identity)
        assert receipt is not None
        expected = (
            "faulted"
            if scenario["boundary"] == "before-dispatch" or scenario["outcome"] == "fault"
            else "completed"
        )
        assert result.status == expected, result.error
        if scenario["boundary"] == "before-dispatch":
            assert "unresolved" in result.error["message"] and receipt.status == "pending"
        else:
            assert receipt.status == ("faulted" if scenario["outcome"] == "fault" else "completed")
            ReceiptDependency.capture(identity, receipt).verify(services.effect_journal)
        duplicate = await engine.resume(parent, {"duplicate": True}, plan)
        write_json(
            directory / "result.json",
            {
                "pid": os.getpid(),
                "invocation_id": parent.invocation_id,
                "operation_id": receipt.operation_id,
                "owner": receipt.owner,
                "request_digest": receipt.request_digest,
                "status": result.status,
                "output": result.output,
                "error": result.error,
                "duplicate": duplicate.as_dict(),
                "original": services.invocations.read_input(parent),
                "receipt_status": receipt.status,
                "has_external_proof": receipt.external_outcome is not None,
                "model_calls": services.model.calls,
            },
        )
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
            failure_artifact(error, stage=mode),
        )
        raise
