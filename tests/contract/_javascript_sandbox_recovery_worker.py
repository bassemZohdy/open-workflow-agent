"""Test-only native producer stopped around a physical JavaScript operation."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest
from _grpc_recovery_worker import native_snapshot
from _process_recovery_worker import write_json
from _recovery_failure_artifact import failure_artifact
from engine_cases import engine_cases
from open_workflow_agent.child_dispatcher import ChildDispatcher
from open_workflow_agent.child_wait_dispatcher import ChildWaitDispatcher
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency
from open_workflow_agent.sandbox import compile_sandbox_workflow
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.timing import root_clock_identity
from test_javascript_sandbox_contract import handle_for, runtime, workflow

CODE = """
const fs = require('node:fs');
const input = fs.readFileSync(0, 'utf8');
const fd = fs.openSync(process.argv[2], 'a');
fs.writeSync(fd, JSON.stringify({pid: process.pid, input: JSON.parse(input)}) + '\\n');
fs.fsyncSync(fd);
fs.closeSync(fd);
if (process.argv[3] === 'pending') {
  const signal = new Int32Array(new SharedArrayBuffer(4));
  while (true) Atomics.wait(signal, 0, 0, 25);
}
process.stdout.write(input);
"""


def rows(directory):
    path = directory / "business.jsonl"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return [json.loads(line) for line in text.splitlines()] if text.endswith("\n") else []


def evidence(services, handle, backend, directory):
    identities = [
        EffectIdentity.from_value(json.loads(row[0]))
        for row in services.effect_journal.connection.execute(
            "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchall()
        if json.loads(row[0])["operation"] == "run:script"
    ]
    assert len(identities) == 1
    receipt = services.effect_journal.get(identities[0])
    clock = services.effect_journal.get(
        root_clock_identity(handle.invocation_id, handle.workflow_fingerprint)
    )
    assert receipt is not None and clock is not None
    owner = services.execution_ownership.connection.execute(
        "SELECT generation,expires_at FROM invocation_ownership WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()
    return {
        "pid": os.getpid(),
        "invocation_id": handle.invocation_id,
        "session_id": handle.session_id,
        "reference": handle.engine_execution_reference,
        "fingerprint": handle.workflow_fingerprint,
        "version": handle.workflow_version,
        "input": services.invocations.read_input(handle),
        "backend": backend,
        "generation": owner[0],
        "lease_expires_at": owner[1],
        "identity": asdict(identities[0]),
        "receipt_status": receipt.status,
        "receipt_digest": ReceiptDependency._digest(receipt),
        "clock": clock.output,
        "native": native_snapshot(services, handle, backend),
        "business": rows(directory),
        "model_calls": len(services.model.calls),
    }


async def run(directory, mode):
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    name, backend, boundary = scenario["engine"], scenario["backend"], scenario["boundary"]
    patches = pytest.MonkeyPatch()

    async def no_delivery(*args):
        return None

    patches.setattr(TimerDispatcher, "start", no_delivery)
    patches.setattr(ChildDispatcher, "tick", no_delivery)
    patches.setattr(ChildWaitDispatcher, "tick", no_delivery)
    services = runtime(directory, backend)
    services.config.sandbox.timeout_seconds = 90
    services.config.workflow.definition = workflow([{"replacement": {"set": {"changed": True}}}])
    services.config.workflow.definition["document"]["version"] = "2.0.0"
    engine = dict(engine_cases())[name]()
    assert engine.native.available if name == "adk" else engine.native.task_nodes_available
    await engine.initialize(services)
    try:
        if mode == "start":
            public = workflow(
                [
                    {
                        "execute": {
                            "run": {
                                "script": {
                                    "language": "javascript",
                                    "code": CODE,
                                    "arguments": [str(directory / "business.jsonl"), boundary],
                                    "stdin": "${ . | tojson }",
                                }
                            }
                        }
                    }
                ]
            )
            public["timeout"] = {"after": {"seconds": 120}}
            plan = compile_sandbox_workflow(public, sandbox=services.config.sandbox)
            handle = handle_for(services, name, plan)
            observe = engine.executor._notify_receipt
            reached = asyncio.Event()

            async def known_receipt(state, identity, receipt):
                await observe(state, identity, receipt)
                if identity.operation == "run:script":
                    write_json(
                        directory / "ready.json", evidence(services, handle, backend, directory)
                    )
                    reached.set()
                    await asyncio.Event().wait()

            if boundary == "known":
                engine.executor._notify_receipt = known_receipt
            active = asyncio.create_task(engine.invoke(plan, handle, scenario["input"]))

            async def committed():
                async with asyncio.timeout(60):
                    while not rows(directory):
                        if active.done():
                            await active
                            raise AssertionError("JavaScript ended before the physical commit")
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
                    active, *([watcher] if watcher else []), return_exceptions=True
                )
        else:
            saved = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
            handle = services.invocations.get(saved["invocation_id"])
            plan = services.resolve_original_definition(handle)
            assert plan.version == "1.0.0"
            async with asyncio.timeout(40):
                while True:
                    owner = services.execution_ownership.connection.execute(
                        "SELECT expires_at FROM invocation_ownership WHERE invocation_id=?",
                        (handle.invocation_id,),
                    ).fetchone()
                    if owner is None or owner[0] <= time.time():
                        break
                    await asyncio.sleep(0.1)
            result = await asyncio.wait_for(engine.resume(handle, {"replacement": True}, plan), 35)
            value = evidence(services, handle, backend, directory)
            value.update(status=result.status, output=result.output, error=result.error)
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
            failure_artifact(error, stage=mode, include_traceback=True),
        )
        raise SystemExit(1) from None
