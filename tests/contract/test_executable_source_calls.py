"""Actual common sandbox, captured source, native checkpoint and backend proofs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from _grpc_recovery_worker import native_snapshot
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import OwaError
from open_workflow_agent.executable_source_admission import SOURCE_POLICY
from open_workflow_agent.executable_sources import ExecutableSource, ExecutableSourceRegistry
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.sandbox import compile_sandbox_workflow
from open_workflow_agent.services import RuntimeServices
from test_workflow_version_recovery import _common_snapshot

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")
ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
URI = "https://captured.test/original.py"
CODE = b"""import json,os,sqlite3,sys
body=sys.stdin.read()
connection=sqlite3.connect(sys.argv[1],timeout=3)
connection.execute('INSERT INTO commits(label,body,pid) VALUES (?,?,?)',
                   (sys.argv[2],body,os.getpid()))
connection.commit()
print(body)
sys.stderr.write('controlled-stderr\\n')
"""


def artifact(code=CODE, **changes):
    return ExecutableSource.capture(
        id=changes.get("id", "original"),
        version=changes.get("version", "1.0.0"),
        uri=changes.get("uri", URI),
        code=code,
        digest=hashlib.sha256(code).hexdigest(),
    )


def configuration(directory, backend, source=None):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is required for actual PostgreSQL")
    return RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "sandbox": {
                "enabled": True,
                "script_sources": [(source or artifact()).as_value()],
                "workspace_root": str(directory / "workspace"),
                "executable_search_path": str(Path(sys.executable).parent),
                "inherited_environment": ["SYSTEMROOT"] if os.name == "nt" else [],
                "file_size_bytes": None if os.name == "nt" else 1048576,
                "process_count": None
                if os.name == "posix" and getattr(os, "getuid", lambda: 0)() == 0
                else 64,
                "timeout_seconds": 15,
            },
        }
    )


def document(tasks, **changes):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "captured-scripts",
            "name": "original",
            "version": "1.0.0",
        },
        "timeout": {"after": {"seconds": 120}},
        "do": tasks,
        **changes,
    }


def action(directory, label="original", mode="stdout"):
    return {
        "input": {"from": "${ $workflow.input }"},
        "run": {
            "return": mode,
            "script": {
                "language": "python",
                "source": {"endpoint": {"uri": URI}},
                "arguments": [str(directory / "business.sqlite3"), label],
                "stdin": "${ . | tojson }",
            },
        },
    }


def compile_original(services, public):
    return compile_sandbox_workflow(
        public,
        sandbox=services.config.sandbox,
        executable_sources=services.executable_sources,
    )


def identities(services, handle, operation="run:script"):
    return [
        identity
        for row in services.effect_journal.connection.execute(
            "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchall()
        if (identity := EffectIdentity.from_value(json.loads(row[0]))).operation == operation
    ]


def ledger(directory):
    with sqlite3.connect(directory / "business.sqlite3", timeout=3) as connection:
        return connection.execute("SELECT label,body,pid FROM commits ORDER BY rowid").fetchall()


def initialize_ledger(directory):
    with sqlite3.connect(directory / "business.sqlite3") as connection:
        # Deliberately no deduplication/unique key: every launch is observable.
        connection.execute("CREATE TABLE commits(label TEXT,body TEXT,pid INTEGER)")


def common_snapshot(services, handle):
    result = _common_snapshot(services, handle.invocation_id)
    result["executable_source_admissions"] = services.effect_journal.connection.execute(
        "SELECT * FROM executable_source_admissions WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchall()
    return result


async def setup(directory, engine_name, engine_type, backend, config=None):
    engine = engine_type()
    if not (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    ):
        pytest.skip("actual independent native SDK environment is required")
    services = RuntimeServices(
        config or configuration(directory, backend),
        model=FakeModel(),
        database_root=None if backend == "postgresql" else directory / "runtime",
    )
    assert (
        services.effect_journal.connection.kind == services.invocations.connection.kind == backend
    )
    await engine.initialize(services)
    return services, engine


def handle_for(services, engine_name, plan):
    return services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="original-user",
        owner_principal="original-owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("scope", ["projections", "nested", "reusable", "loop"])
async def test_captured_bytes_execute_in_common_sandbox_with_exact_input_and_native_identity(
    tmp_path, monkeypatch, engine_name, engine_type, backend, scope
):
    initialize_ledger(tmp_path)
    services, engine = await setup(tmp_path, engine_name, engine_type, backend)
    requests = []

    async def forbidden(*args, **kwargs):
        raise AssertionError("a declared source URI must never be fetched")

    monkeypatch.setattr(HttpClient, "request", forbidden)
    execute = services.sandbox.execute

    async def common_execute(request):
        requests.append(request)
        return await execute(request)

    monkeypatch.setattr(services.sandbox, "execute", common_execute)
    original = None if scope == "nested" else {"original": "π", "nullValue": None, "items": [1, 2]}
    if scope == "projections":
        tasks = []
        for mode in ("stdout", "stderr", "code", "all", "none"):
            task = action(tmp_path, mode, mode)
            task["export"] = {"as": "${ $context + {" + mode + ": .} }"}
            tasks.append({mode: task})
        tasks.append(
            {
                "result": {
                    "set": {
                        mode: "${ $context." + mode + " }"
                        for mode in ("stdout", "stderr", "code", "all", "none")
                    }
                }
            }
        )
        public = document(tasks)
        count = 5
    elif scope == "nested":
        public = document([{"group": {"do": [{"script": action(tmp_path)}]}}])
        count = 1
    elif scope == "reusable":
        public = document(
            [{"one": {"call": "trusted"}}, {"two": {"call": "trusted"}}],
            use={"functions": {"trusted": action(tmp_path)}},
        )
        count = 2
    else:
        public = document(
            [
                {
                    "repeat": {
                        "for": {"each": "turn", "in": [10, 20]},
                        "do": [{"script": action(tmp_path)}],
                    }
                }
            ]
        )
        count = 2
    plan = compile_original(services, public)
    handle = handle_for(services, engine_name, plan)
    try:
        result = await asyncio.wait_for(engine.invoke(plan, handle, original), 45)
        assert result.status == "completed", result.error
        if scope == "projections":
            assert json.loads(result.output["stdout"]) == original
            assert result.output["stderr"] == "controlled-stderr\n"
            assert result.output["code"] == 0 and result.output["none"] is None
            assert result.output["all"] == {
                "stdout": result.output["stdout"],
                "stderr": "controlled-stderr\n",
                "code": 0,
            }
        else:
            assert json.loads(result.output) == original
        rows = ledger(tmp_path)
        assert len(rows) == len(requests) == count
        assert all(json.loads(row[1]) == original and row[2] != os.getpid() for row in rows)
        assert all(request.script_code.encode("utf-8") == CODE for request in requests)
        assert all(
            Path(request.arguments[0]) == tmp_path / "business.sqlite3" for request in requests
        )
        business = identities(services, handle)
        policies = identities(services, handle, SOURCE_POLICY)
        assert (
            len(business) == len(policies) == count
            and len({item.key for item in business}) == count
        )
        assert {replace(item, operation=SOURCE_POLICY) for item in business} == set(policies)
        assert all(
            services.effect_journal.get(item).status == "completed" for item in policies + business
        )
        assert all(item.source == artifact() for item in plan.script_resources)
        assert plan.source == public and CODE.decode() not in json.dumps(public)
        assert any(value["count"] for value in native_snapshot(services, handle, backend).values())
        assert services.invocations.read_input(handle) == original and services.model.calls == []
        if scope == "reusable":
            assert len(plan.script_resources) == 1
            assert {item.task_reference for item in business} == {
                "/do/0/one/call/trusted",
                "/do/1/two/call/trusted",
            }
        if scope == "loop":
            assert len({item.occurrence for item in business}) == 2
    finally:
        await engine.shutdown()
        services.close()


async def interrupted_known(directory, engine_name, engine_type, backend):
    initialize_ledger(directory)
    services, engine = await setup(directory, engine_name, engine_type, backend)
    plan = compile_original(
        services,
        document([{"script": action(directory)}, {"pause": {"wait": {"seconds": 300}}}]),
    )
    handle = handle_for(services, engine_name, plan)
    try:
        result = await asyncio.wait_for(
            engine.invoke(plan, handle, {"original": True, "nullValue": None}), 40
        )
        assert result.status == "waiting", result.error
        assert len(ledger(directory)) == 1
        assert any(
            event.event_type == "TaskCompleted" and event.task_reference == "/do/0/script"
            for event in services.events.events
        )
        # The actual native engine has committed the completed script followed
        # by a real timer interruption. Corruption cannot be bypassed by that
        # saved completed task or its independently retained receipt link.
        assert any(value["count"] for value in native_snapshot(services, handle, backend).values())
    finally:
        await engine.shutdown()
    return services, engine, plan, handle


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("damage", ["registry", "policy-and-index", "input"])
async def test_native_known_receipt_cannot_bypass_original_source_authority_before_mutation(
    tmp_path, engine_name, engine_type, backend, damage
):
    services, _engine, plan, handle = await interrupted_known(
        tmp_path, engine_name, engine_type, backend
    )
    services.close()
    services, engine = await setup(tmp_path, engine_name, engine_type, backend)
    handle = services.invocations.get(handle.invocation_id)
    assert handle is not None and handle.status not in {"completed", "faulted", "cancelled"}
    original_rows = ledger(tmp_path)
    policies = identities(services, handle, SOURCE_POLICY)
    assert len(policies) == 1
    changes = (
        [
            artifact(CODE + b"# changed\n"),
            artifact(id="changed"),
            artifact(version="2.0.0"),
            artifact(uri="https://captured.test/changed.py"),
        ]
        if damage == "registry"
        else ["policy", "index"]
        if damage == "policy-and-index"
        else ["input"]
    )
    try:
        for change in changes:
            saved = None
            if damage == "registry":
                services.executable_sources = ExecutableSourceRegistry((change,))
            else:
                table = (
                    "workflow_effects"
                    if change == "policy"
                    else "workflow_effect_invocations"
                    if change == "index"
                    else "invocation_inputs"
                )
                key, value = (
                    ("invocation_id", handle.invocation_id)
                    if change == "input"
                    else ("identity", policies[0].key)
                )
                connection = services.effect_journal.connection
                saved = connection.execute(
                    f"SELECT * FROM {table} WHERE {key}=?", (value,)
                ).fetchone()
                connection.execute(f"DELETE FROM {table} WHERE {key}=?", (value,))
                connection.commit()
            before = common_snapshot(services, handle)
            before_native = native_snapshot(services, handle, backend)
            with pytest.raises(OwaError):
                await engine.resume(
                    handle, {"must-not-replace": True}, services.resolve_original_definition(handle)
                )
            assert common_snapshot(services, handle) == before
            assert native_snapshot(services, handle, backend) == before_native
            assert ledger(tmp_path) == original_rows and services.model.calls == []
            if saved is not None:
                connection.execute(
                    f"INSERT INTO {table} VALUES ({','.join('?' for _ in saved)})", tuple(saved)
                )
                connection.commit()
    finally:
        await engine.shutdown()
        services.close()
