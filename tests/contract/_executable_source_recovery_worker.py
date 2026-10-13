"""Real native owner stopped around an actual captured Python business process."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path

import pytest
from _grpc_recovery_worker import native_snapshot
from _process_recovery_worker import write_json
from engine_cases import engine_cases
from open_workflow_agent.child_dispatcher import ChildDispatcher
from open_workflow_agent.child_wait_dispatcher import ChildWaitDispatcher
from open_workflow_agent.effects import ReceiptDependency
from open_workflow_agent.errors import InvocationStateConflict, OwaError
from open_workflow_agent.executable_source_admission import SOURCE_POLICY
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.timing import root_clock_identity
from test_executable_source_calls import (
    action,
    artifact,
    common_snapshot,
    compile_original,
    configuration,
    document,
    handle_for,
    identities,
    ledger,
    setup,
)

CODE = b"""import os,sqlite3,sys,time
from pathlib import Path
directory=Path(sys.argv[1]).parent
body=sys.stdin.read()
connection=sqlite3.connect(sys.argv[1],timeout=3)
connection.execute('INSERT INTO commits(label,body,pid) VALUES (?,?,?)',
                   (sys.argv[2],body,os.getpid()))
connection.commit()
if (directory/'hold-business').exists():
 while not (directory/'release-business').exists(): time.sleep(.025)
print(body)
sys.stderr.write('controlled-stderr\\n')
"""


def evidence(services, handle, backend, directory):
    business = identities(services, handle)
    policies = identities(services, handle, SOURCE_POLICY)
    assert len(business) == len(policies) == 1
    receipt = services.effect_journal.get(business[0])
    policy = services.effect_journal.get(policies[0])
    clock = services.effect_journal.get(
        root_clock_identity(handle.invocation_id, handle.workflow_fingerprint)
    )
    assert receipt is not None and policy is not None and clock is not None
    owner = services.execution_ownership.connection.execute(
        "SELECT generation,expires_at FROM invocation_ownership WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()
    rows = ledger(directory)
    return {
        "pid": os.getpid(),
        "invocation_id": handle.invocation_id,
        "session_id": handle.session_id,
        "reference": handle.engine_execution_reference,
        "fingerprint": handle.workflow_fingerprint,
        "version": handle.workflow_version,
        "owner": handle.owner_principal,
        "input": services.invocations.read_input(handle),
        "backend": services.effect_journal.connection.kind,
        "generation": owner[0],
        "lease_expires_at": owner[1],
        "identity": asdict(business[0]),
        "receipt_status": receipt.status,
        "intent_digest": ReceiptDependency._digest(receipt),
        "receipt_digest": ReceiptDependency.capture(business[0], receipt).receipt_digest
        if receipt.status != "pending"
        else None,
        "policy_digest": ReceiptDependency.capture(policies[0], policy).receipt_digest,
        "source_binding": policy.output["source_binding"],
        "deadline_at": policy.output["deadline_at"],
        "clock": clock.output,
        "native": native_snapshot(services, handle, backend),
        "commits": len(rows),
        "child_pid": rows[0][2] if rows else None,
        "model_calls": len(services.model.calls),
    }


async def run(directory, mode):
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    name, backend, boundary = scenario["engine"], scenario["backend"], scenario["boundary"]
    patches = pytest.MonkeyPatch()

    async def no_delivery(*args):
        return None

    async def forbidden_fetch(*args, **kwargs):
        raise AssertionError("captured executable source must never be fetched")

    patches.setattr(TimerDispatcher, "start", no_delivery)
    patches.setattr(ChildDispatcher, "tick", no_delivery)
    patches.setattr(ChildWaitDispatcher, "tick", no_delivery)
    patches.setattr(HttpClient, "request", forbidden_fetch)
    selected = (
        artifact(CODE + b"# revoked\n")
        if mode == "recover" and scenario["damage"] == "registry"
        else artifact(CODE)
    )
    config = configuration(directory, backend, selected)
    config.sandbox.timeout_seconds = 90
    config.workflow.definition = document([{"new-deployment": {"set": {"version": "2.0.0"}}}])
    config.workflow.definition["document"]["version"] = "2.0.0"
    engine_type = dict(engine_cases())[name]
    services, engine = await setup(directory, name, engine_type, backend, config)
    try:
        if mode == "start":
            plan = compile_original(services, document([{"script": action(directory)}]))
            handle = handle_for(services, name, plan)
            observe = engine.executor._notify_receipt
            execute = services.sandbox.execute
            reached = asyncio.Event()

            async def before_dispatch(request):
                write_json(directory / "ready.json", evidence(services, handle, backend, directory))
                reached.set()
                await asyncio.Event().wait()
                return await execute(request)

            async def known_receipt(state, identity, receipt):
                await observe(state, identity, receipt)
                if identity.operation == "run:script":
                    write_json(
                        directory / "ready.json", evidence(services, handle, backend, directory)
                    )
                    reached.set()
                    await asyncio.Event().wait()

            if boundary == "before-dispatch":
                services.sandbox.execute = before_dispatch
            elif boundary == "known":
                engine.executor._notify_receipt = known_receipt
            active = asyncio.create_task(
                engine.invoke(plan, handle, {"subject": scenario["subject"], "nullValue": None})
            )

            async def committed():
                async with asyncio.timeout(60):
                    while not ledger(directory):
                        assert not active.done(), "script owner ended before real business commit"
                        await asyncio.sleep(0.02)
                write_json(directory / "ready.json", evidence(services, handle, backend, directory))
                reached.set()

            watcher = asyncio.create_task(committed()) if boundary == "pending" else None
            try:
                await asyncio.wait_for(reached.wait(), 65)
                await asyncio.Event().wait()
            finally:
                active.cancel()
                if watcher is not None:
                    watcher.cancel()
                await asyncio.gather(
                    active, *([watcher] if watcher is not None else []), return_exceptions=True
                )
        else:
            saved = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
            handle = services.invocations.get(saved["invocation_id"])
            assert handle is not None and handle.status == "running"
            if scenario["damage"] in {"policy", "index"}:
                policy = identities(services, handle, SOURCE_POLICY)[0]
                table = (
                    "workflow_effects"
                    if scenario["damage"] == "policy"
                    else "workflow_effect_invocations"
                )
                services.effect_journal.connection.execute(
                    f"DELETE FROM {table} WHERE identity=?", (policy.key,)
                )
                services.effect_journal.connection.commit()
            common_before = common_snapshot(services, handle)
            native_before = native_snapshot(services, handle, backend)
            rows_before = ledger(directory)
            if scenario["damage"] != "none":
                try:
                    await engine.resume(
                        handle, {"replace": True}, services.resolve_original_definition(handle)
                    )
                except OwaError:
                    pass
                else:
                    raise AssertionError("damaged source authority accepted native continuation")
                write_json(
                    directory / "result.json",
                    {
                        "pid": os.getpid(),
                        "rejected_unchanged": common_snapshot(services, handle) == common_before
                        and native_snapshot(services, handle, backend) == native_before,
                        "commits": len(ledger(directory)),
                        "model_calls": len(services.model.calls),
                    },
                )
            else:
                plan = services.resolve_original_definition(handle)
                assert plan.version == "1.0.0" and plan.script_resources[0].source == artifact(CODE)
                # Natural expiry of the original 30s lease. No clock patch,
                # fabricated generation, ownership rewrite or shortened lease.
                async with asyncio.timeout(40):
                    while True:
                        row = services.execution_ownership.connection.execute(
                            "SELECT expires_at FROM invocation_ownership WHERE invocation_id=?",
                            (handle.invocation_id,),
                        ).fetchone()
                        if row is None or row[0] <= time.time():
                            break
                        assert common_snapshot(services, handle) == common_before
                        assert native_snapshot(services, handle, backend) == native_before
                        await asyncio.sleep(0.1)
                try:
                    result = await asyncio.wait_for(
                        engine.resume(handle, {"replace": True}, plan), 35
                    )
                except InvocationStateConflict:
                    raise AssertionError(
                        "actual original lease expired but continuation could not acquire"
                    ) from None
                value = evidence(services, handle, backend, directory)
                value.update(status=result.status, output=result.output, error=result.error)
                assert ledger(directory) == rows_before
                write_json(directory / "result.json", value)
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
            {
                "stage": mode,
                "type": type(error).__name__,
                "code": getattr(error, "code", None),
                "frames": [
                    {"file": Path(item.filename).name, "line": item.lineno, "function": item.name}
                    for item in traceback.extract_tb(error.__traceback__)[-8:]
                ],
            },
        )
        raise SystemExit(1) from None
