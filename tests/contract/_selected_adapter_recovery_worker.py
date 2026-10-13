"""Real OS-stop proof for the finite embedded codec/commit/ACK seam."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from _process_recovery_worker import write_json
from _selected_adapter_fixtures import (
    RAW,
    actual_native,
    admit,
    authority,
    create,
    definition,
    invoke,
    known,
    message,
    runtime,
)
from _version_recovery_worker import native_snapshot
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.errors import OwaError
from open_workflow_agent.event_transport import CommittedEventAdmission
from open_workflow_agent.execution_authority import authority_scope, current_authority
from open_workflow_agent.timing import root_clock_identity
from open_workflow_agent.workflow import compile_workflow


def ledger(directory):
    with sqlite3.connect(directory / "provider.sqlite3") as db:
        return db.execute("SELECT prompt,pid FROM calls ORDER BY rowid").fetchall()


async def run(directory, mode):
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    backend, name, boundary = scenario["backend"], scenario["engine"], scenario["boundary"]
    module, cls = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[name]
    engine = getattr(importlib.import_module(module), cls)()
    actual_native(engine, name)
    with sqlite3.connect(directory / "provider.sqlite3") as db:
        db.execute("CREATE TABLE IF NOT EXISTS calls(prompt TEXT,pid INTEGER)")

    async def response(prompt):
        assert current_authority().principal.identity == "alice"
        assert prompt["prompt"] == {"events": [None], "original": RAW}
        with sqlite3.connect(directory / "provider.sqlite3") as db:
            db.execute("INSERT INTO calls VALUES (?,?)", (json.dumps(prompt), os.getpid()))
        return {"events": [None], "original": RAW}

    services = runtime(directory / "runtime", backend, FakeModel(response))
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    engine._execution_ownership_seconds = 5
    wire = None
    admission = None
    handle = None

    async def park():
        assert handle is not None
        lease = handle._execution_lease
        assert lease is not None
        services.execution_ownership.verify(lease)
        native = native_snapshot(services, handle, backend)
        assert native["present"]
        clock = services.effect_journal.get(
            root_clock_identity(handle.invocation_id, plan.fingerprint)
        )
        write_json(
            directory / "ready.json",
            {
                "pid": os.getpid(),
                "invocation_id": handle.invocation_id,
                "backend": backend,
                "original_input": services.invocations.read_input(handle),
                "authority": services.execution_authorizations.verify_invocation(handle).as_value(),
                "fingerprint": plan.fingerprint,
                "clock": clock.output,
                "receipts": known(services, handle),
                "native": native,
                "provider_count": len(ledger(directory)),
                "wire": wire.as_dict() if wire else None,
                "admission": {
                    "scope": admission.scope,
                    "sequence": admission.sequence,
                    "receipt_digest": admission.receipt_digest,
                }
                if admission
                else None,
            },
        )
        while not (directory / "ack.json").exists():
            await asyncio.sleep(0.01)
        assert json.loads((directory / "ack.json").read_text())["pid"] == os.getpid()
        write_json(directory / "acknowledged.json", {"pid": os.getpid()})
        await asyncio.Event().wait()

    try:
        if mode == "start":
            wire = message(services, scenario["subject"], data=None, user="bob", role="admin")
            properties = wire.properties()
            tasks = [
                {
                    "receive": {
                        "listen": {
                            "to": {
                                "one": {
                                    "with": {
                                        "type": "selected.accepted",
                                        "subject": scenario["subject"],
                                    }
                                }
                            }
                        }
                    }
                },
                {
                    "action": {
                        "call": "llm:1.0.0@default",
                        "with": {"prompt": "${ {events:.,original:$workflow.input} }"},
                    }
                },
            ]
            if boundary == "outbox":
                tasks.insert(0, {"publish": {"emit": {"event": {"with": properties}}}})
            plan = compile_workflow(definition(tasks, name=scenario["subject"]))
            handle = create(services, name, plan)
            observer = engine.executor._notify_receipt

            async def notify(state, identity, receipt):
                await observer(state, identity, receipt)
                target = {
                    "outbox": "emit",
                    "listen-known": "listen",
                    "model-known": "call:llm:1.0.0@default",
                }.get(boundary)
                if identity.invocation_id == handle.invocation_id and (
                    identity.operation == target and receipt.status == "completed"
                ):
                    await park()

            engine.executor._notify_receipt = notify
            if boundary in {"listen-known", "model-known"}:
                admission = admit(services, wire)
            task = asyncio.create_task(invoke(engine, services, plan, handle))
            if boundary == "inbox-before-ack":
                expires = time.monotonic() + 40
                while services.invocations.get(handle.invocation_id).status != "waiting":
                    assert not task.done() and time.monotonic() < expires
                    await asyncio.sleep(0.02)

                async def ack(committed):
                    nonlocal admission
                    admission = committed
                    # ACK is intentionally NOT completed before the OS stop.
                    assert len(ledger(directory)) == 0
                    await park()

                with authority_scope(authority(services, "alice")):
                    await services.event_transport.admit_and_ack(
                        wire.encoded, scope="alice", acknowledge=ack, timeout_seconds=60
                    )
            await task
            raise AssertionError("producer passed its exact durable boundary")
        else:
            ready = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
            handle = services.invocations.get(ready["invocation_id"])
            plan = services.resolve_original_definition(handle)
            assert plan.fingerprint == ready["fingerprint"]
            if ready["admission"]:
                wire = services.event_transport.codec.capture(ready["wire"])
                admission = CommittedEventAdmission(message=wire, **ready["admission"])
                # Trusted transport re-delivery verifies its own retained proof
                # before invoking native recovery. This is NOT a claim that SDK
                # checkpoints contain a new transport dependency protocol.
                services.event_transport.verify(admission)
                with authority_scope(authority(services, "alice")):
                    assert services.event_transport.admit(wire.encoded, scope="alice") == admission
            expires = time.monotonic() + 15
            while services.execution_ownership.connection.execute(
                "SELECT 1 FROM invocation_ownership WHERE invocation_id=? AND expires_at>"
                + services.execution_ownership._clock,
                (handle.invocation_id,),
            ).fetchone():
                assert time.monotonic() < expires
                await asyncio.sleep(0.02)
            result = await engine.resume(handle, {"replace": "untrusted"}, plan)
            assert result.status == "completed", result.error
            assert result.output == {"events": [None], "original": RAW}
            assert services.invocations.read_input(handle) == ready["original_input"] == RAW
            assert (
                services.execution_authorizations.verify_invocation(handle).as_value()
                == ready["authority"]
            )
            clock = services.effect_journal.get(
                root_clock_identity(handle.invocation_id, plan.fingerprint)
            )
            assert clock.output == ready["clock"]
            after = known(services, handle)
            assert all(after[key] == proof for key, proof in ready["receipts"].items())
            assert len(ledger(directory)) == 1
            assert len(services.model.calls) == (0 if boundary == "model-known" else 1)
            assert await engine.resume(handle, {}, plan) == result and len(ledger(directory)) == 1
            write_json(
                directory / "result.json",
                {
                    "pid": os.getpid(),
                    "producer_pid": ready["pid"],
                    "status": result.status,
                    "original_input": services.invocations.read_input(handle),
                    "clock": clock.output,
                    "authority": ready["authority"],
                    "provider_count": len(ledger(directory)),
                    "new_model_calls": len(services.model.calls),
                    "backend": backend,
                    "admission": ready["admission"],
                },
            )
    finally:
        await engine.shutdown()
        services.close()
        if mode == "start":
            write_json(directory / "start-cleaned.json", {"pid": os.getpid()})


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    try:
        asyncio.run(run(directory, sys.argv[2]))
    except BaseException as error:
        write_json(
            directory / (sys.argv[2] + "-failure.json"),
            {
                "exception": type(error).__name__,
                "error": error.as_dict() if isinstance(error, OwaError) else None,
            },
        )
        raise SystemExit(1) from None
