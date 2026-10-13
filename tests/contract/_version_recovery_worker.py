"""Owned real producer killed around common definition/native admission boundaries."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from _process_recovery_worker import write_json
from open_workflow_agent.storage import StorageConnection
from test_synchronous_child_waits import _clock
from test_workflow_version_recovery import (
    TOKEN,
    TOKEN_ENV,
    Receiver,
    _common_snapshot,
    _config,
    _definition,
    _services,
)


def native_snapshot(services: Any, handle: Any, backend: str) -> dict[str, Any]:
    """Read an existing native database without creating namespaces or SDK tables."""
    if backend == "sqlite":
        path = Path(services.engine_database_path(handle.engine, create_namespace=False))
        if not path.exists():
            return {"present": False}
        connection = StorageConnection(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True), "sqlite"
        )
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
    else:
        psycopg = importlib.import_module("psycopg")
        connection = StorageConnection(
            psycopg.connect(services.datasource, autocommit=True), "postgresql"
        )
        namespace = "owa_" + handle.engine
        assert namespace in {"owa_adk", "owa_langgraph"}
        connection.execute("SET search_path TO " + namespace + ",public")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=? ORDER BY table_name",
                (namespace,),
            ).fetchall()
        ]
    try:
        result: dict[str, Any] = {"present": True, "tables": tables}
        for table in tables:
            assert table.isascii() and table.isidentifier()
            rows = sorted(connection.execute('SELECT * FROM "' + table + '"').fetchall(), key=repr)
            result[table] = hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()
        return result
    finally:
        connection.close()


async def run(directory: Path, mode: str) -> None:
    from open_workflow_agent.api import create_app

    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    os.environ[TOKEN_ENV] = TOKEN
    patches = pytest.MonkeyPatch()
    _clock(patches, [1000.0])
    engine_module, engine_type = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[scenario["engine"]]
    engine = getattr(importlib.import_module(engine_module), engine_type)()
    assert (
        engine.native.available
        if scenario["engine"] == "adk"
        else engine.native.task_nodes_available
    )
    receiver = Receiver(directory / "receiver.sqlite3")
    config = _config(_definition("1.0.0" if mode == "start" else "2.0.0"), scenario["backend"])
    services = _services(directory / "runtime", config, receiver)
    app = create_app(config=config, services=services, engine=engine)
    original = {"request": scenario["subject"], "original": True}
    headers = {"Authorization": "Bearer " + TOKEN}

    def ready(handle: Any) -> None:
        write_json(
            directory / "ready.json",
            {
                "pid": os.getpid(),
                "invocation_id": handle.invocation_id,
                "fingerprint": handle.workflow_fingerprint,
                "version": handle.workflow_version,
                "session_id": handle.session_id,
                "native_available": True,
                "original": services.invocations.read_input(handle),
                "binding_present": services.recovery_definitions.has_binding(handle),
                "remote_commits": len(receiver.rows()),
            },
        )

    if mode == "start" and scenario["boundary"] == "archive":
        admit = services.recovery_definitions.admit

        def archive_boundary(
            plan: Any, handle: Any, input_data: Any, *, execution_lease: Any
        ) -> None:
            admit(plan, handle, input_data, execution_lease=execution_lease)
            ready(handle)
            # A real parent SIGKILL/TerminateProcess ends this producer; no
            # graceful cancellation or cleanup proves this committed boundary.
            while True:
                time.sleep(0.1)

        patches.setattr(services.recovery_definitions, "admit", archive_boundary)
    try:
        async with app.router.lifespan_context(app):
            assert app.state.plan.version == ("1.0.0" if mode == "start" else "2.0.0")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
            ) as client:
                if mode == "start":
                    response = await client.post("/v1/invoke", json={"input": original})
                    assert response.status_code == 200 and response.json()["status"] == "waiting"
                    handle = services.invocations.get(response.json()["invocation_id"])
                    assert handle is not None
                    ready(handle)
                    await asyncio.Event().wait()
                saved = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
                handle = services.invocations.get(saved["invocation_id"])
                assert handle is not None and handle.workflow_version == "1.0.0"
                assert services.invocations.read_input(handle) == original
                before = _common_snapshot(services, handle.invocation_id)
                native_before = native_snapshot(services, handle, scenario["backend"])
                response = await client.post(
                    f"/v1/invocations/{handle.invocation_id}/resume", json={"input": None}
                )
                if scenario["boundary"] == "archive":
                    assert response.status_code == 409, response.text
                    assert response.json()["error"]["code"] == "invalid_invocation_state"
                    assert _common_snapshot(services, handle.invocation_id) == before
                    assert native_snapshot(services, handle, scenario["backend"]) == native_before
                    assert receiver.calls == [] and receiver.rows() == []
                    # A deployment owner may explicitly redeliver verified
                    # initial admission. Public resume never guesses this path.
                    async with asyncio.timeout(90):
                        while True:
                            current = services.invocations.get(handle.invocation_id)
                            plan = services.resolve_original_definition(current)
                            try:
                                result = await engine.invoke(plan, current, original)
                                break
                            except Exception as error:
                                from open_workflow_agent.errors import InvocationStateConflict

                                if not isinstance(error, InvocationStateConflict):
                                    raise
                                await asyncio.sleep(0.1)
                    assert result.status == "waiting" and len(receiver.rows()) == 1
                    response = await client.post(
                        f"/v1/invocations/{handle.invocation_id}/resume", json={"input": None}
                    )
                assert response.status_code == 200 and response.json()["status"] == "completed", (
                    response.text
                )
                assert response.json()["output"] == {
                    "version": "1.0.0",
                    "original": original,
                    "resume": None,
                }
                assert response.json()["session_id"] == saved["session_id"]
                fresh = await client.post("/v1/invoke", json={"input": {"new": True}})
                assert fresh.status_code == 200 and fresh.json()["output"]["version"] == "2.0.0"
                assert len(receiver.rows()) == 2
                assert len(receiver.calls) == (2 if scenario["boundary"] == "archive" else 1)
                retained = services.invocations.get(handle.invocation_id)
                assert (
                    retained is not None and retained.workflow_fingerprint == saved["fingerprint"]
                )
                write_json(
                    directory / "result.json",
                    {
                        "pid": os.getpid(),
                        "invocation_id": handle.invocation_id,
                        "session_id": retained.session_id,
                        "fingerprint": retained.workflow_fingerprint,
                        "version": retained.workflow_version,
                        "original": services.invocations.read_input(retained),
                        "output": response.json()["output"],
                        "model_calls": services.model.calls,
                        "remote_commits": len(receiver.rows()),
                        "archive_only_rejected_without_mutation": scenario["boundary"] == "archive",
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
            directory / (mode + "-failure.json"), {"type": type(error).__name__, "stage": mode}
        )
        raise
