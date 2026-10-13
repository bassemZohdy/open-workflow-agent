"""Test-only real native producer/recovery owner; no receiver replay shortcut."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from _grpc_recovery_peer import URI, binding, commits
from _process_recovery_worker import write_json
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.child_dispatcher import ChildDispatcher
from open_workflow_agent.child_wait_dispatcher import ChildWaitDispatcher
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency
from open_workflow_agent.errors import InvocationStateConflict, OwaError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import StorageConnection
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.timing import root_clock_identity
from open_workflow_agent.workflow import compile_workflow
from test_durable_timers import _use_clock
from test_workflow_version_recovery import _common_snapshot


def source(port, version, pending):
    definition = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "stopped-grpc",
            "name": "peer",
            "version": version,
        },
        "do": [
            {
                "guard": {
                    "timeout": {"after": {"seconds": 120}},
                    "try": [
                        {
                            "business": {
                                "call": "grpc",
                                "with": {
                                    "proto": {"endpoint": URI},
                                    "service": {
                                        "name": "stopped.Peer",
                                        "host": "127.0.0.1",
                                        "port": port,
                                    },
                                    "method": "Call",
                                    "arguments": {
                                        "value": {"version": version, "original": "${ . }"}
                                    },
                                },
                                "output": {"as": "${ .value }"},
                            }
                        }
                    ],
                    "catch": {
                        "errors": {"with": {"type": "${ true }"}},
                        "retry": {
                            "limit": {"attempt": {"count": 3}},
                            "delay": {"milliseconds": 1},
                            "backoff": {"constant": {}},
                        },
                    },
                }
            }
        ],
    }
    if not pending:
        scope = definition["do"][0]["guard"]
        scope["do"] = scope.pop("try")
        del scope["catch"]
    return definition


def native_snapshot(services, handle, backend):
    """Existing native rows only; no SDK initialization or schema migration."""
    path = services.engine_database_path(handle.engine, create_namespace=False)
    if backend == "sqlite":
        connection = StorageConnection(
            sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True), "sqlite"
        )
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
    else:
        connection = StorageConnection(
            importlib.import_module("psycopg").connect(services.datasource, autocommit=True),
            "postgresql",
        )
        namespace = "owa_" + handle.engine
        assert namespace in {"owa_adk", "owa_langgraph"}
        connection.execute("SET search_path TO " + namespace + ",public")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=?",
                (namespace,),
            ).fetchall()
        ]
    try:
        scope = hashlib.sha256(
            json.dumps(
                [
                    "owa/native-scopes/v1",
                    handle.invocation_id,
                    handle.workflow_fingerprint,
                    "/do",
                    1,
                ]
            ).encode()
        ).hexdigest()
        selected = (
            [("sessions", "id"), ("events", "session_id")]
            if handle.engine == "adk"
            else [
                (name, "thread_id")
                for name in ("checkpoints", "writes", "checkpoint_writes", "checkpoint_blobs")
            ]
        )
        keys = (
            (handle.session_id, handle.session_id)
            if handle.engine == "adk"
            else (handle.invocation_id, "owa-scope-v1:" + scope)
        )
        return {
            name: {"count": len(rows), "hash": hashlib.sha256(repr(rows).encode()).hexdigest()}
            for name, column in selected
            if name in tables
            for rows in [
                sorted(
                    connection.execute(
                        f"SELECT * FROM {name} WHERE {column} IN (?,?)", keys
                    ).fetchall(),
                    key=repr,
                )
            ]
        }
    finally:
        connection.close()


def identities(services, handle):
    rows = services.effect_journal.connection.execute(
        "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchall()
    return [
        identity
        for row in rows
        if (identity := EffectIdentity.from_value(json.loads(row[0]))).operation == "call:grpc"
    ]


def evidence(services, engine, handle, backend, directory):
    business = identities(services, handle)
    assert len(business) == 1
    receipt = services.effect_journal.get(business[0])
    clock = services.effect_journal.get(
        root_clock_identity(handle.invocation_id, handle.workflow_fingerprint)
    )
    deadlines = []
    rows = services.effect_journal.connection.execute(
        "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchall()
    for row in rows:
        identity = EffectIdentity.from_value(json.loads(row[0]))
        if identity.operation == "policy:task-deadline:v1":
            deadlines.append(services.effect_journal.get(identity).output)
    generation = services.execution_ownership.connection.execute(
        "SELECT generation FROM invocation_ownership WHERE invocation_id=?", (handle.invocation_id,)
    ).fetchone()[0]
    return {
        "pid": os.getpid(),
        "native_available": True,
        "backend": backend,
        "invocation_id": handle.invocation_id,
        "session_id": handle.session_id,
        "reference": handle.engine_execution_reference,
        "fingerprint": handle.workflow_fingerprint,
        "version": handle.workflow_version,
        "owner": handle.owner_principal,
        "input": services.invocations.read_input(handle),
        "generation": generation,
        "identity": asdict(business[0]),
        "receipt_status": receipt.status,
        "receipt_digest": ReceiptDependency.capture(business[0], receipt).receipt_digest
        if receipt.status != "pending"
        else None,
        "intent_digest": ReceiptDependency._digest(receipt),
        "operation_id": receipt.operation_id,
        "clock": clock.output,
        "deadlines": deadlines,
        "native": native_snapshot(services, handle, backend),
        "grpc_profile": services.grpc_registry.snapshot(),
        "commits": len(commits(directory)),
        "model_calls": len(services.model.calls),
    }


async def run(directory, mode):
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    peer = json.loads((directory / "receiver-ready.json").read_text(encoding="utf-8"))
    name, backend = scenario["engine"], scenario["backend"]
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {
                "datasource": os.getenv("OWA_TEST_POSTGRES_URL")
                if backend == "postgresql"
                else None
            },
            "workflow": {
                "definition": source(
                    peer["port"],
                    "1.0.0" if mode == "start" else "2.0.0",
                    scenario["boundary"] == "pending",
                )
            },
            "protocols": {"grpc_bindings": {"original-peer": binding(peer["endpoint"])}},
        }
    )
    patches = pytest.MonkeyPatch()
    _use_clock(patches, lambda: 1000.0 if mode == "start" else 1005.0)

    async def no_delivery(*args):
        return None

    patches.setattr(TimerDispatcher, "start", no_delivery)
    patches.setattr(ChildDispatcher, "tick", no_delivery)
    patches.setattr(ChildWaitDispatcher, "tick", no_delivery)
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if backend == "postgresql" else directory
    )
    assert (
        services.invocations.connection.kind == services.effect_journal.connection.kind == backend
    )
    services.protocols.http.allowed_hosts = {"127.0.0.1"}
    module, classname = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[name]
    engine = getattr(importlib.import_module(module), classname)()
    assert engine.native.available if name == "adk" else engine.native.task_nodes_available
    await engine.initialize(services)
    engine._execution_ownership_seconds = 5
    try:
        if mode == "start":
            plan = compile_workflow(config.workflow.definition, grpc_enabled=services.grpc_enabled)
            handle = services.invocations.create(
                engine=name,
                session_id=None,
                user_id="original-user",
                owner_principal="original-owner",
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            original_call = engine.executor._call

            async def hold_known(call, payload, state):
                try:
                    output = await original_call(call, payload, state)
                except OwaError:
                    write_json(
                        directory / "ready.json",
                        evidence(services, engine, handle, backend, directory),
                    )
                    await asyncio.Event().wait()
                    raise
                write_json(
                    directory / "ready.json", evidence(services, engine, handle, backend, directory)
                )
                await asyncio.Event().wait()
                return output

            if scenario["boundary"] == "known":
                engine.executor._call = hold_known
            running = asyncio.create_task(
                engine.invoke(plan, handle, {"request": scenario["subject"]})
            )
            watcher = None
            if scenario["boundary"] == "pending":

                async def at_commit():
                    async with asyncio.timeout(60):
                        while not commits(directory):
                            await asyncio.sleep(0.02)
                    write_json(
                        directory / "ready.json",
                        evidence(services, engine, handle, backend, directory),
                    )

                watcher = asyncio.create_task(at_commit())
            try:
                if watcher is not None:
                    await asyncio.wait_for(watcher, 65)
                await asyncio.wait_for(running, 90)
                raise AssertionError("producer exited instead of holding its stop boundary")
            finally:
                running.cancel()
                if watcher is not None:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                await asyncio.gather(running, return_exceptions=True)
        else:
            saved = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
            handle = services.invocations.get(saved["invocation_id"])
            assert handle is not None and handle.status == "running"
            if scenario["negative"] == "archive-binding":
                services.invocations.connection.execute(
                    "DELETE FROM recovery_invocation_definitions WHERE invocation_id=?",
                    (handle.invocation_id,),
                )
                services.invocations.connection.commit()
            elif scenario["negative"] == "descriptor-profile":
                before = config.protocols.grpc_bindings["original-peer"]
                config.protocols.grpc_bindings["original-peer"] = before.model_copy(
                    update={"descriptor_set_base64": "dGFtcGVyZWQ="}
                )
            native_before = native_snapshot(services, handle, backend)
            common_before = _common_snapshot(services, handle.invocation_id)
            if scenario["negative"] != "none":
                try:
                    await asyncio.wait_for(
                        engine.resume(
                            handle, {"changed": True}, services.resolve_original_definition(handle)
                        ),
                        15,
                    )
                except (OwaError, ValueError):
                    pass
                else:
                    raise AssertionError("damaged admitted evidence was accepted")
                write_json(
                    directory / "result.json",
                    {
                        "pid": os.getpid(),
                        "rejected_unchanged": native_snapshot(services, handle, backend)
                        == native_before
                        and _common_snapshot(services, handle.invocation_id) == common_before,
                        "commits": len(commits(directory)),
                        "model_calls": len(services.model.calls),
                    },
                )
            else:
                plan = services.resolve_original_definition(handle)
                async with asyncio.timeout(25):
                    while True:
                        try:
                            result = await engine.resume(handle, {"changed": True}, plan)
                            break
                        except InvocationStateConflict:
                            assert native_snapshot(services, handle, backend) == native_before
                            assert _common_snapshot(services, handle.invocation_id) == common_before
                            await asyncio.sleep(0.05)
                value = evidence(services, engine, handle, backend, directory)
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
            {"stage": mode, "type": type(error).__name__, "code": getattr(error, "code", None)},
        )
        raise
