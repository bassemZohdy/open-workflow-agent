"""A real SDK-acknowledged producer is killed; a different native owner recovers."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest
from _process_recovery_worker import write_json
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.types.a2a_pb2 import AgentCard, GetTaskRequest, SendMessageRequest
from google.protobuf.json_format import MessageToDict, ParseDict
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.child_dispatcher import ChildDispatcher
from open_workflow_agent.child_wait_dispatcher import ChildWaitDispatcher
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency
from open_workflow_agent.errors import InvocationStateConflict, OwaError
from open_workflow_agent.initial_delivery_dispatcher import InitialDeliveryDispatcher
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import StorageConnection
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.timing import root_clock_identity
from test_a2a_sdk_inbound import listening
from test_synchronous_child_waits import _clock
from test_workflow_version_recovery import _common_snapshot

TOKEN = "deterministic-initial-delivery-token"


def native_snapshot(services, handle, backend):
    """Pure existing-schema reads scoped to this invocation's native identity."""
    path = services.engine_database_path(handle.engine, create_namespace=False)
    if backend == "sqlite":
        if not Path(path).exists():
            return {"present": False, "rows": {}}
        connection = StorageConnection(
            sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True), "sqlite"
        )
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
    else:
        pg = importlib.import_module("psycopg")
        connection = StorageConnection(
            pg.connect(services.datasource, autocommit=True), "postgresql"
        )
        namespace = "owa_" + handle.engine
        assert namespace in {"owa_adk", "owa_langgraph"}
        connection.execute("SET search_path TO " + namespace + ",public")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=? "
                "ORDER BY table_name",
                (namespace,),
            ).fetchall()
        ]
    try:
        selected = (
            [("sessions", "id"), ("events", "session_id")]
            if handle.engine == "adk"
            else [
                (name, "thread_id")
                for name in ("checkpoints", "writes", "checkpoint_writes", "checkpoint_blobs")
            ]
        )
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
        keys = (
            (handle.session_id, handle.session_id)
            if handle.engine == "adk"
            else (
                handle.invocation_id,
                "owa-scope-v1:" + scope,
            )
        )
        rows = {
            name: sorted(
                connection.execute(
                    f"SELECT * FROM {name} WHERE {column} IN (?,?)", keys
                ).fetchall(),
                key=repr,
            )
            for name, column in selected
            if name in tables
        }
        return {
            "present": True,
            "tables": tables,
            "rows": {
                name: {
                    "count": len(values),
                    "hash": hashlib.sha256(repr(values).encode()).hexdigest(),
                }
                for name, values in rows.items()
            },
        }
    finally:
        connection.close()


async def run(directory: Path, mode: str):
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    engine_name, backend, boundary = scenario["engine"], scenario["backend"], scenario["boundary"]
    saved = (
        None
        if mode == "start"
        else json.loads((directory / "ready.json").read_text(encoding="utf-8"))
    )
    patches = pytest.MonkeyPatch()
    _clock(patches, [1000.0 if mode == "start" else 1010.0])
    os.environ["OWA_A2A_INITIAL_TOKEN"] = TOKEN

    async def no_delivery(_dispatcher, *args, **kwargs):
        return None

    patches.setattr(TimerDispatcher, "start", no_delivery)
    for cls in (ChildDispatcher, ChildWaitDispatcher):
        patches.setattr(cls, "tick", no_delivery)
    if mode == "recover":

        async def manual_delivery(dispatcher, authorize):
            dispatcher._authorize = authorize

        patches.setattr(InitialDeliveryDispatcher, "start", manual_delivery)

    version = "1.0.0" if mode == "start" else "2.0.0"
    source = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "a2a-initial",
            "name": "job-" + scenario["subject"],
            "version": version,
        },
        "timeout": {"after": {"seconds": 60}},
        "do": [
            {"prior": {"set": {"prior": True}}},
            {
                "commit": {
                    "call": "http",
                    "with": {
                        "method": "POST",
                        "endpoint": "https://initial.test/action",
                        "body": {"version": version, "original": "${ $workflow.input }"},
                    },
                }
            },
            {"finish": {"set": {"version": version, "original": "${ $workflow.input }"}}},
        ],
    }
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    assert backend != "postgresql" or datasource
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "workflow": {"definition": source},
            "a2a": {
                "enabled": True,
                "security_profile": "caller",
                "skills": [{"id": "retained", "workflow": source["document"]["name"]}],
                "authorization": {
                    "rules": [
                        {
                            "actions": ["message.send", "tasks.get", "tasks.cancel"],
                            "resources": ["skill:retained", "tasks"],
                            "roles": ["test-users"],
                        }
                    ]
                },
            },
            "security": {
                "profiles": {
                    "caller": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_A2A_INITIAL_TOKEN"},
                        "principal": "initial-owner",
                        "roles": ["test-users"],
                    }
                }
            },
        }
    )
    model = FakeModel()
    services = RuntimeServices(
        config, model=model, database_root=None if datasource else directory / "runtime"
    )
    assert (
        services.invocations.connection.kind == services.effect_journal.connection.kind == backend
    )
    module, cls = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[engine_name]
    engine = getattr(importlib.import_module(module), cls)()
    assert engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    engine._execution_ownership_seconds = 5
    with closing(sqlite3.connect(directory / "receiver.sqlite3")) as receiver:
        receiver.execute("CREATE TABLE IF NOT EXISTS commits(operation_id TEXT,body TEXT)")
        receiver.commit()

    def commits():
        with closing(sqlite3.connect(directory / "receiver.sqlite3")) as receiver:
            return receiver.execute(
                "SELECT operation_id,body FROM commits ORDER BY rowid"
            ).fetchall()

    async def receiver(request):
        with closing(sqlite3.connect(directory / "receiver.sqlite3")) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO commits(operation_id,body) VALUES (?,?)",
                    (request.headers["Idempotency-Key"], request.content.decode()),
                )
        return httpx.Response(200, json={"accepted": json.loads(request.content)})

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    services.tools.protocols = services.protocols
    reached = asyncio.Event()
    action_identity = None
    budgets = []
    if mode == "start" and boundary == "before-sdk":
        invoke = engine._invoke_owned

        async def before_sdk(*args, **kwargs):
            reached.set()
            await asyncio.Event().wait()
            return await invoke(*args, **kwargs)

        patches.setattr(engine, "_invoke_owned", before_sdk)
    if mode == "start" and boundary == "partial-native":
        native_module = importlib.import_module(module + ".native")
        owner = getattr(
            native_module,
            ("_FencedDatabaseSessionService" if datasource else "_ClosingSqliteSessionService")
            if engine_name == "adk"
            else ("_FencedAsyncPostgresSaver" if datasource else "_FencedAsyncSqliteSaver"),
        )
        method = "append_event" if engine_name == "adk" else "aput"
        original_write = getattr(owner, method)

        async def projection(instance, *args, **kwargs):
            result = await original_write(instance, *args, **kwargs)
            if not reached.is_set():
                reached.set()
                await asyncio.Event().wait()
            return result

        patches.setattr(owner, method, projection)
    app = create_app(config=config, services=services, engine=engine)
    try:
        async with listening(app) as base:
            real_notify = engine.executor._notify_receipt

            async def receipt_boundary(state, identity, receipt):
                nonlocal action_identity
                if identity.operation == "call:http":
                    action_identity = identity
                    if mode == "start" and boundary == "known-receipt":
                        assert services.effect_journal.get(identity).status == "completed"
                        reached.set()
                        await asyncio.Event().wait()
                await real_notify(state, identity, receipt)

            patches.setattr(engine.executor, "_notify_receipt", receipt_boundary)
            real_timed = engine.executor._run_timed

            async def timed(operation, seconds, message, details, *, absolute_due=None, state=None):
                if absolute_due is not None:
                    budgets.append([seconds, absolute_due])
                return await real_timed(
                    operation, seconds, message, details, absolute_due=absolute_due, state=state
                )

            patches.setattr(engine.executor, "_run_timed", timed)
            async with httpx.AsyncClient(
                timeout=15, headers={"A2A-Version": "1.0", "Authorization": "Bearer " + TOKEN}
            ) as client:
                card = ParseDict(
                    (await client.get(base + "/.well-known/agent-card.json")).json(),
                    AgentCard(),
                    ignore_unknown_fields=False,
                )
                peer = JsonRpcTransport(client, card, base + "/a2a")
                if mode == "start":
                    request = ParseDict(
                        {
                            "message": {
                                "messageId": "message-" + scenario["subject"],
                                "contextId": "context-" + scenario["subject"],
                                "role": "ROLE_USER",
                                "metadata": {"skillId": "retained"},
                                "parts": [{"text": scenario["original"]}],
                            },
                            "configuration": {"returnImmediately": True},
                        },
                        SendMessageRequest(),
                        ignore_unknown_fields=False,
                    )
                    ack = await peer.send_message(request)
                    assert ack.HasField("task")
                    handle = services.invocations.get(ack.task.id)
                    await asyncio.wait_for(reached.wait(), timeout=30)
                    native = native_snapshot(services, handle, backend)
                    clock = services.effect_journal.get(
                        root_clock_identity(handle.invocation_id, handle.workflow_fingerprint)
                    )
                    receipt = (
                        services.effect_journal.get(action_identity) if action_identity else None
                    )
                    write_json(
                        directory / "ready.json",
                        {
                            "pid": os.getpid(),
                            "native_available": True,
                            "invocation_id": handle.invocation_id,
                            "session_id": handle.session_id,
                            "owner": handle.owner_principal,
                            "reference": handle.engine_execution_reference,
                            "version": handle.workflow_version,
                            "fingerprint": handle.workflow_fingerprint,
                            "input": services.invocations.read_input(handle),
                            "clock": clock.output,
                            "native": native,
                            "commits": len(commits()),
                            "receipt_digest": ReceiptDependency.capture(
                                action_identity, receipt
                            ).receipt_digest
                            if receipt
                            else None,
                            "receipt_identity": asdict(action_identity)
                            if action_identity
                            else None,
                            "model_calls": len(model.calls),
                            "delivery": services.initial_deliveries.get(handle).status,
                            "generation": services.invocations.connection.execute(
                                "SELECT generation FROM invocation_ownership WHERE invocation_id=?",
                                (handle.invocation_id,),
                            ).fetchone()[0],
                        },
                    )
                    await asyncio.Event().wait()
                handle = services.invocations.get(saved["invocation_id"])
                assert services.invocations.read_input(handle) == saved["input"]
                dispatcher = engine._initial_delivery_dispatcher
                negative = scenario["negative"]
                if negative == "revoked":
                    del app.state.a2a_skill_plans["retained"]
                elif negative == "archive":
                    services.effect_journal.connection.execute(
                        "DELETE FROM recovery_invocation_definitions WHERE invocation_id=?",
                        (handle.invocation_id,),
                    )
                    services.effect_journal.connection.commit()
                elif negative == "queue":
                    services.effect_journal.connection.execute(
                        "UPDATE initial_invocation_deliveries SET digest='damaged' "
                        "WHERE invocation_id=?",
                        (handle.invocation_id,),
                    )
                    services.effect_journal.connection.commit()
                before = {
                    "common": _common_snapshot(services, handle.invocation_id),
                    "queue": services.effect_journal.connection.execute(
                        "SELECT * FROM initial_invocation_deliveries WHERE invocation_id=?",
                        (handle.invocation_id,),
                    ).fetchall(),
                    "native": native_snapshot(services, handle, backend),
                    "commits": commits(),
                }
                if negative != "none":
                    try:
                        await engine.dispatch_initial_delivery(handle, dispatcher._verify_authority)
                    except OwaError:
                        pass
                    else:
                        raise AssertionError("invalid delivery did not reject")
                    after = {
                        "common": _common_snapshot(services, handle.invocation_id),
                        "queue": services.effect_journal.connection.execute(
                            "SELECT * FROM initial_invocation_deliveries WHERE invocation_id=?",
                            (handle.invocation_id,),
                        ).fetchall(),
                        "native": native_snapshot(services, handle, backend),
                        "commits": commits(),
                    }
                    assert after == before and model.calls == []
                    write_json(
                        directory / "result.json",
                        {"pid": os.getpid(), "rejected_unchanged": True, "commits": len(commits())},
                    )
                    return
                async with asyncio.timeout(30):
                    while True:
                        try:
                            result = await engine.dispatch_initial_delivery(
                                handle, dispatcher._verify_authority
                            )
                            break
                        except InvocationStateConflict:
                            await asyncio.sleep(0.05)
                assert result.status == "completed", result.error
                task = MessageToDict(await peer.get_task(GetTaskRequest(id=handle.invocation_id)))
                assert task["status"]["state"] == "TASK_STATE_COMPLETED"
                assert task["artifacts"][0]["parts"][0]["data"] == {
                    "version": "1.0.0",
                    "original": saved["input"],
                }
                clock = services.effect_journal.get(
                    root_clock_identity(handle.invocation_id, handle.workflow_fingerprint)
                )
                assert (
                    clock.output
                    == saved["clock"]
                    == {"profile": "owa/timing/v1", "started_at": 1000.0}
                )
                assert [50.0, 1060.0] in budgets
                if saved["receipt_identity"]:
                    identity = EffectIdentity.from_value(saved["receipt_identity"])
                    assert (
                        ReceiptDependency.capture(
                            identity, services.effect_journal.get(identity)
                        ).receipt_digest
                        == saved["receipt_digest"]
                    )
                retained = services.invocations.get(handle.invocation_id)
                assert services.initial_deliveries.get(retained).status == "settled"
                duplicate = await engine.dispatch_initial_delivery(
                    retained, dispatcher._verify_authority
                )
                assert duplicate.as_dict() == result.as_dict()
                assert len(commits()) == 1 and model.calls == []
                write_json(
                    directory / "result.json",
                    {
                        "pid": os.getpid(),
                        "invocation_id": retained.invocation_id,
                        "session_id": retained.session_id,
                        "reference": retained.engine_execution_reference,
                        "version": retained.workflow_version,
                        "fingerprint": retained.workflow_fingerprint,
                        "owner": retained.owner_principal,
                        "input": services.invocations.read_input(retained),
                        "output": retained.output,
                        "clock": clock.output,
                        "commits": len(commits()),
                        "model_calls": len(model.calls),
                        "delivery": services.initial_deliveries.get(retained).status,
                        "generation": services.invocations.connection.execute(
                            "SELECT generation FROM invocation_ownership WHERE invocation_id=?",
                            (retained.invocation_id,),
                        ).fetchone()[0],
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
            {"stage": mode, "type": type(error).__name__, "code": getattr(error, "code", None)},
        )
        raise
