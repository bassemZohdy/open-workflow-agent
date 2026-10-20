"""Worker processes for the initial-delivery owner-generation race witness."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.recovery_definitions import PROFILE, RecoveryDefinitionArchive
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import compile_workflow


def _write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _snapshot(services: RuntimeServices, invocation_id: str) -> dict[str, list[list[Any]]]:
    connection = services.invocations.connection
    queries = {
        "invocation": "SELECT * FROM invocations WHERE invocation_id=?",
        "ownership": "SELECT * FROM invocation_ownership WHERE invocation_id=?",
        "delivery": "SELECT * FROM initial_invocation_deliveries WHERE invocation_id=?",
    }
    return {
        name: [list(row) for row in connection.execute(query, (invocation_id,)).fetchall()]
        for name, query in queries.items()
    }


async def _run(directory: Path, mode: str) -> None:
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    backend = scenario["backend"]
    source = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "a2a-initial-process-race",
            "name": "delivery-race",
            "version": "1.0.0",
        },
        "do": [
            {
                "commit": {
                    "call": "http",
                    "with": {
                        "method": "POST",
                        "endpoint": "https://initial-process-race.test/delivery",
                        "body": {"input": "${ $workflow.input }"},
                    },
                }
            },
            {"finish": {"set": "${ $workflow.input }"}},
        ],
    }
    patches = pytest.MonkeyPatch()

    async def no_timer_worker(_dispatcher: TimerDispatcher) -> None:
        return None

    patches.setattr(TimerDispatcher, "start", no_timer_worker)
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {
                "datasource": os.getenv("OWA_TEST_POSTGRES_URL")
                if backend == "postgresql"
                else None
            },
            "workflow": {"definition": source},
        }
    )
    model = FakeModel()
    services = RuntimeServices(
        config,
        model=model,
        database_root=directory / "runtime" if backend == "sqlite" else None,
    )
    services.recovery_definitions = RecoveryDefinitionArchive(
        services.effect_journal,
        services.invocations,
        profile_provider=lambda engine: {
            "profile": PROFILE,
            "engine": engine,
            "backend": backend,
            "artifact": "initial-delivery-process-owner-fence",
            "continuation_supported": True,
        },
    )

    def save_commit(request: httpx.Request) -> httpx.Response:
        if mode.startswith("claimant-"):
            _write(
                directory / "receiver-entered.json",
                {
                    "pid": os.getpid(),
                    "snapshot": _snapshot(services, scenario["invocation_id"]),
                },
            )
            deadline = time.monotonic() + 300
            while not (directory / "release-receiver").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("test did not release competing receiver")
                time.sleep(0.02)
        with closing(sqlite3.connect(directory / "receiver.sqlite3", timeout=10)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO commits(operation_id,body) VALUES (?,?)",
                    (request.headers["Idempotency-Key"], request.content.decode()),
                )
        return httpx.Response(200, json={"accepted": True})

    with closing(sqlite3.connect(directory / "receiver.sqlite3", timeout=10)) as receiver:
        receiver.execute("CREATE TABLE IF NOT EXISTS commits(operation_id TEXT,body TEXT)")
        receiver.commit()
    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(save_commit)))
    services.tools.protocols = services.protocols
    engine = PortableWorkflowEngine()
    # The test expires the durable row directly after the old process is paused.
    # A long lease avoids a timer renewal racing that deliberate takeover.
    engine._execution_ownership_seconds = 180
    await engine.initialize(services)
    plan = compile_workflow(source)

    def authorize(_handle: Any, _plan: Any) -> None:
        return None

    try:
        if mode in {"old", "competing-owner", "race-prepare"}:
            handle = services.invocations.create(
                engine="base",
                session_id="initial-delivery-process-race",
                user_id="race-user",
                owner_principal="race-owner",
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            scenario["invocation_id"] = handle.invocation_id
            _write(directory / "scenario.json", scenario)
            await engine.prepare_initial_delivery(
                plan, handle, scenario["input"], skill_id="process-race"
            )
            if mode == "race-prepare":
                _write(
                    directory / "race-prepared.json",
                    {
                        "pid": os.getpid(),
                        "invocation_id": handle.invocation_id,
                        "snapshot": _snapshot(services, handle.invocation_id),
                    },
                )
                return
            if mode == "competing-owner":
                original_invoke = engine._invoke_owned

                async def pause_competing_owner(*args: Any, **kwargs: Any):
                    lease = args[1]._execution_lease
                    assert lease is not None
                    _write(
                        directory / "owner-ready.json",
                        {
                            "pid": os.getpid(),
                            "invocation_id": args[1].invocation_id,
                            "generation": lease.generation,
                            "snapshot": _snapshot(services, args[1].invocation_id),
                        },
                    )
                    deadline = time.monotonic() + 300
                    while not (directory / "release-owner").exists():
                        if time.monotonic() >= deadline:
                            raise TimeoutError("test did not release competing owner")
                        await asyncio.sleep(0.02)
                    return await original_invoke(*args, **kwargs)

                patches.setattr(engine, "_invoke_owned", pause_competing_owner)
                result = await engine.dispatch_initial_delivery(handle, authorize)
                delivery = services.initial_deliveries.get(handle)
                assert delivery is not None
                with closing(sqlite3.connect(directory / "receiver.sqlite3")) as receiver:
                    commits = receiver.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
                _write(
                    directory / "owner-result.json",
                    {
                        "pid": os.getpid(),
                        "status": result.status,
                        "output": result.output,
                        "generation": services.invocations.connection.execute(
                            "SELECT generation FROM invocation_ownership WHERE invocation_id=?",
                            (handle.invocation_id,),
                        ).fetchone()[0],
                        "delivery_status": delivery.status,
                        "commits": commits,
                        "snapshot": _snapshot(services, handle.invocation_id),
                    },
                )
                return
            original_invoke = engine._invoke_owned

            async def pause_old(*args: Any, **kwargs: Any):
                lease = args[1]._execution_lease
                assert lease is not None
                _write(
                    directory / "old-ready.json",
                    {
                        "pid": os.getpid(),
                        "invocation_id": args[1].invocation_id,
                        "generation": lease.generation,
                    },
                )
                deadline = time.monotonic() + 300
                while not (directory / "release-old").exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("test did not release paused old owner")
                    await asyncio.sleep(0.02)
                return await original_invoke(*args, **kwargs)

            patches.setattr(engine, "_invoke_owned", pause_old)
            try:
                await engine.dispatch_initial_delivery(handle, authorize)
            except InvocationStateConflict as error:
                result = {
                    "pid": os.getpid(),
                    "error_type": type(error).__name__,
                    "snapshot": _snapshot(services, handle.invocation_id),
                }
            else:
                raise AssertionError("stale initial-delivery owner was not fenced")
            with closing(sqlite3.connect(directory / "receiver.sqlite3")) as receiver:
                result["commits"] = receiver.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
            _write(directory / "old-result.json", result)
        elif mode in {"current", "competing-worker"}:
            handle = services.invocations.get(scenario["invocation_id"])
            assert handle is not None
            if mode == "competing-worker":
                try:
                    await engine.dispatch_initial_delivery(handle, authorize)
                except InvocationStateConflict as error:
                    _write(
                        directory / "competitor-result.json",
                        {
                            "pid": os.getpid(),
                            "error_type": type(error).__name__,
                            "snapshot": _snapshot(services, handle.invocation_id),
                        },
                    )
                else:
                    raise AssertionError("competing initial-delivery owner was not fenced")
            else:
                result = await engine.dispatch_initial_delivery(handle, authorize)
                delivery = services.initial_deliveries.get(handle)
                assert delivery is not None
                with closing(sqlite3.connect(directory / "receiver.sqlite3")) as receiver:
                    commits = receiver.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
                _write(
                    directory / "current-result.json",
                    {
                        "pid": os.getpid(),
                        "status": result.status,
                        "output": result.output,
                        "generation": services.invocations.connection.execute(
                            "SELECT generation FROM invocation_ownership WHERE invocation_id=?",
                            (handle.invocation_id,),
                        ).fetchone()[0],
                        "delivery_status": delivery.status,
                        "commits": commits,
                        "snapshot": _snapshot(services, handle.invocation_id),
                    },
                )
        elif mode.startswith("claimant-"):
            handle = services.invocations.get(scenario["invocation_id"])
            assert handle is not None
            _write(
                directory / f"{mode}-ready.json",
                {"pid": os.getpid(), "snapshot": _snapshot(services, handle.invocation_id)},
            )
            deadline = time.monotonic() + 300
            while not (directory / "release-dispatch").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("test did not release simultaneous dispatch claimants")
                await asyncio.sleep(0.02)
            try:
                result = await engine.dispatch_initial_delivery(handle, authorize)
            except InvocationStateConflict as error:
                payload = {
                    "pid": os.getpid(),
                    "outcome": "conflict",
                    "error_type": type(error).__name__,
                    "snapshot": _snapshot(services, handle.invocation_id),
                }
            else:
                delivery = services.initial_deliveries.get(handle)
                assert delivery is not None
                with closing(sqlite3.connect(directory / "receiver.sqlite3")) as receiver:
                    commits = receiver.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
                payload = {
                    "pid": os.getpid(),
                    "outcome": "completed",
                    "status": result.status,
                    "output": result.output,
                    "generation": services.invocations.connection.execute(
                        "SELECT generation FROM invocation_ownership WHERE invocation_id=?",
                        (handle.invocation_id,),
                    ).fetchone()[0],
                    "delivery_status": delivery.status,
                    "commits": commits,
                    "snapshot": _snapshot(services, handle.invocation_id),
                }
            _write(directory / f"{mode}-result.json", payload)
        else:
            raise ValueError(f"unknown worker mode: {mode}")
    finally:
        await engine.shutdown()
        services.close()
        patches.undo()


if __name__ == "__main__":
    worker_directory, worker_mode = Path(sys.argv[1]), sys.argv[2]
    try:
        asyncio.run(_run(worker_directory, worker_mode))
    except BaseException as error:
        _write(
            worker_directory / (worker_mode + "-failure.json"),
            {"type": type(error).__name__, "message": str(error)},
        )
        raise
