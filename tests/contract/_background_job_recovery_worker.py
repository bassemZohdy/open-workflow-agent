"""Actual native producer and independent supervisor, stopped by the owning test."""

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

from _process_recovery_worker import write_json
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import OwaError
from open_workflow_agent.sandbox import compile_sandbox_workflow
from open_workflow_agent.sandbox._job_ipc import read_frame, read_key
from open_workflow_agent.services import RuntimeServices
from test_native_checkpoint_fencing import _snapshot as raw_native_snapshot

SCRIPT = """import json,os,sqlite3,sys,time
from pathlib import Path
root=Path(sys.argv[1])
temporary=root/'child-ready.tmp'
temporary.write_text(json.dumps({'pid':os.getpid()}))
temporary.replace(root/'child-ready.json')
while not (root/'release-job').exists(): time.sleep(.025)
connection=sqlite3.connect(root/'business.sqlite3')
connection.execute('INSERT INTO commits(value) VALUES (?)', (sys.stdin.read(),))
connection.commit()
print('real independent job completed')
"""


def native_snapshot(services, handle, backend):
    # Actual SQLite checkpoint cells include msgpack bytes. Preserve their exact
    # identity without serializing a native checkpoint as an execution input.
    # Normalize SQL row tuples only for the test's persisted equality witness.
    def cell(value):
        if isinstance(value, bytes):
            return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
        assert value is None or type(value) in {str, int, float}
        return value

    return {
        table: [[cell(value) for value in row] for row in rows]
        for table, rows in raw_native_snapshot(services, handle, backend).items()
    }


def configuration(directory):
    return RuntimeConfig.model_validate(
        {
            "sandbox": {
                "enabled": True,
                "background": {"enabled": True},
                "timeout_seconds": 120,
                "workspace_root": str(directory / "workspace"),
                "executable_search_path": str(Path(sys.executable).parent),
                "inherited_environment": ["SYSTEMROOT"] if os.name == "nt" else [],
                "file_size_bytes": None if os.name == "nt" else 1048576,
                "process_count": None if os.name == "posix" and os.getuid() == 0 else 64,
            }
        }
    )


async def run(directory, mode):
    scenario = json.loads((directory / "scenario.json").read_text())
    saved = None if mode == "start" else json.loads((directory / "ready.json").read_text())
    services = RuntimeServices(
        configuration(directory), model=FakeModel(), database_root=directory / "runtime"
    )
    assert services.effect_journal.connection.kind == "sqlite"
    if scenario.get("damage"):

        async def no_observer():
            return None

        services._background_dispatcher.start = no_observer
    if mode != "start" and scenario.get("damage") == "profile":
        services.config.sandbox.timeout_seconds = 121
    module, name = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[scenario["engine"]]
    engine = getattr(importlib.import_module(module), name)()
    await engine.initialize(services)
    engine._execution_ownership_seconds = 5
    plan = compile_sandbox_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "background-stop",
                "name": "independent",
                "version": "1.0.0",
            },
            "do": [
                {
                    "job": {
                        "input": {"from": "${ {retained: .value, nullValue: null} }"},
                        "run": {
                            "await": False,
                            "return": "none",
                            "script": {
                                "language": "python",
                                "code": SCRIPT,
                                "arguments": [str(directory)],
                                "stdin": "${ . | tojson }",
                            },
                        },
                    }
                }
            ],
        },
        sandbox=services.config.sandbox,
    )
    try:
        if mode == "start":
            handle = services.invocations.create(
                engine=scenario["engine"],
                session_id="original",
                user_id="original",
                owner_principal="original-owner",
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            reached = asyncio.Event()
            original = engine.executor._execute_background

            async def barrier(request, state):
                output = await original(request, state)
                reached.set()
                if scenario["boundary"] != "completed":
                    await asyncio.Event().wait()
                return output

            engine.executor._execute_background = barrier
            active = asyncio.create_task(engine.invoke(plan, handle, {"value": "original"}))
            await asyncio.wait_for(reached.wait(), 40)
            if scenario["boundary"] == "completed":
                result = await asyncio.wait_for(active, 40)
                assert result.status == "completed" and result.output == {
                    "retained": "original",
                    "nullValue": None,
                }
            if scenario.get("damage"):
                identifier = services.effect_journal.connection.execute(
                    "SELECT job_id FROM sandbox_jobs WHERE invocation_id=?", (handle.invocation_id,)
                ).fetchone()[0]
                job = services.sandbox_jobs.get(identifier)
                assert job is not None and job.status == "ready" and job.submit_json is None
                assert not (directory / "child-ready.json").exists()
                write_json(
                    directory / "ready.json",
                    {
                        "producer_pid": os.getpid(),
                        "invocation_id": handle.invocation_id,
                        "job_id": job.job_id,
                        "admission_digest": job.admission_digest,
                        "deadline_at": json.loads(job.admission_json)["deadline_at"],
                        "native": native_snapshot(services, handle, "sqlite"),
                        "fingerprint": plan.fingerprint,
                        "boundary": scenario["boundary"],
                    },
                )
                await asyncio.Event().wait()
            deadline = time.monotonic() + 40
            while not (directory / "child-ready.json").exists():
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
            child = json.loads((directory / "child-ready.json").read_text())
            identifier = services.effect_journal.connection.execute(
                "SELECT job_id FROM sandbox_jobs WHERE invocation_id=?", (handle.invocation_id,)
            ).fetchone()[0]
            job = services.sandbox_jobs.get(identifier)
            deadline = time.monotonic() + 15
            while job is not None and job.status != "accepted":
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
                job = services.sandbox_jobs.get(identifier)
            assert job is not None and job.status == "accepted"
            supervisor = (
                Path(services.config.sandbox.workspace_root) / ".owa-job-supervisors" / job.job_id
            )
            observation = read_frame(
                supervisor / "observation.json", read_key(supervisor / "auth.key")
            )
            assert observation["state"] == "running"
            assert len({os.getpid(), observation["pid"], child["pid"]}) == 3
            if scenario["boundary"] == "cancelled":
                result = await engine.cancel(handle)
                assert result.status == "cancelled"
            if scenario["boundary"] == "drained":
                await engine.shutdown()
            current = services.sandbox_jobs.get(job.job_id)
            assert current.control_json is None and current.terminal_json is None
            assert any(native_snapshot(services, handle, "sqlite").values())
            write_json(
                directory / "ready.json",
                {
                    "producer_pid": os.getpid(),
                    "supervisor_pid": observation["pid"],
                    "child_pid": child["pid"],
                    "invocation_id": handle.invocation_id,
                    "job_id": job.job_id,
                    "admission_digest": job.admission_digest,
                    "deadline_at": json.loads(job.admission_json)["deadline_at"],
                    "native": native_snapshot(services, handle, "sqlite"),
                    "boundary": scenario["boundary"],
                },
            )
            await asyncio.Event().wait()
        else:
            handle = services.invocations.get(saved["invocation_id"])
            assert handle is not None
            if scenario.get("damage"):
                before_native = native_snapshot(services, handle, "sqlite")
                before_owner = services.execution_ownership.connection.execute(
                    "SELECT * FROM invocation_ownership WHERE invocation_id=?",
                    (handle.invocation_id,),
                ).fetchone()
                before_delivery = services.effect_journal.connection.execute(
                    "SELECT * FROM sandbox_job_delivery WHERE job_id=?", (saved["job_id"],)
                ).fetchone()
                try:
                    await asyncio.wait_for(engine.resume(handle, {}, plan), 20)
                except OwaError:
                    pass
                else:
                    raise AssertionError(
                        "damaged original background proof accepted native recovery"
                    )
                assert (
                    native_snapshot(services, handle, "sqlite") == before_native == saved["native"]
                )
                assert (
                    services.execution_ownership.connection.execute(
                        "SELECT * FROM invocation_ownership WHERE invocation_id=?",
                        (handle.invocation_id,),
                    ).fetchone()
                    == before_owner
                )
                assert (
                    services.effect_journal.connection.execute(
                        "SELECT * FROM sandbox_job_delivery WHERE job_id=?", (saved["job_id"],)
                    ).fetchone()
                    == before_delivery
                )
                assert not (directory / "child-ready.json").exists()
                assert not (
                    Path(services.config.sandbox.workspace_root)
                    / ".owa-job-supervisors"
                    / saved["job_id"]
                ).exists()
                with sqlite3.connect(directory / "business.sqlite3") as receiver:
                    assert receiver.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 0
                assert services.model.calls == []
                write_json(
                    directory / "result.json",
                    {
                        "recovery_pid": os.getpid(),
                        "rejected": True,
                        "owner": list(before_owner),
                        "job_id": saved["job_id"],
                        "commits": 0,
                    },
                )
                return
            before = services.sandbox_jobs.get(saved["job_id"])
            assert before is not None and before.admission_digest == saved["admission_digest"]
            assert json.loads(before.admission_json)["deadline_at"] == saved["deadline_at"]
            deadline = time.monotonic() + 15
            while True:
                lease = services.execution_ownership.connection.execute(
                    "SELECT expires_at FROM invocation_ownership WHERE invocation_id=?",
                    (handle.invocation_id,),
                ).fetchone()
                if lease is None or lease[0] <= time.time():
                    break
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
            result = await asyncio.wait_for(
                engine.resume(handle, {"value": "must-not-replace-original"}, plan), 40
            )
            assert result.status == (
                "cancelled" if scenario["boundary"] == "cancelled" else "completed"
            )
            if result.status == "completed":
                assert result.output == {"retained": "original", "nullValue": None}
            assert services.sandbox_jobs.get(saved["job_id"]).control_json is None
            (directory / "release-job").touch()
            deadline = time.monotonic() + 25
            while True:
                await services._background_dispatcher.tick()
                current = services.sandbox_jobs.get(saved["job_id"])
                if current.status == "terminal":
                    break
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
            terminal = json.loads(current.terminal_json)
            assert terminal["reason"] == "exited" and terminal["outcome"]["exit_code"] == 0
            assert current.control_json is None and current.control_ack_json is None
            with sqlite3.connect(directory / "business.sqlite3") as receiver:
                commits = receiver.execute("SELECT value FROM commits").fetchall()
            assert len(commits) == 1 and json.loads(commits[0][0]) == {
                "retained": "original",
                "nullValue": None,
            }
            assert current.admission_digest == saved["admission_digest"]
            assert json.loads(current.admission_json)["deadline_at"] == saved["deadline_at"]
            assert services.model.calls == []
            write_json(
                directory / "result.json",
                {
                    "recovery_pid": os.getpid(),
                    "result": result.as_dict(),
                    "job_id": current.job_id,
                    "admission_digest": current.admission_digest,
                    "commits": len(commits),
                    "control": current.control_json,
                    "deadline_at": saved["deadline_at"],
                },
            )
    finally:
        await engine.shutdown()
        services.close()


if __name__ == "__main__":
    directory = Path(sys.argv[1]).resolve()
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
