"""Detached returns are durable admission decisions, never fabricated outputs."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.child_dispatcher import ChildDispatcher
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


def _document(name: str) -> dict[str, str]:
    return {"dsl": "1.0.3", "namespace": "detached-children", "name": name, "version": "1.0.0"}


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("reopen", [False, True])
async def test_detached_child_returns_task_input_and_survives_parent_completion(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    reopen: bool,
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and datasource is None:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
    calls: list[str] = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def action(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Idempotency-Key"])
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"child_result": "different-from-parent"})

    def services() -> RuntimeServices:
        value = RuntimeServices(
            config, model=FakeModel(), database_root=None if datasource else tmp_path
        )
        value.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(action)))
        value.tools.protocols = value.protocols
        value.workflow_catalog.register(
            {
                "document": _document("child"),
                "do": [
                    {
                        "action": {
                            "call": "http",
                            "with": {
                                "method": "POST",
                                "endpoint": "https://child.test/action",
                                "body": "${ . }",
                            },
                        }
                    }
                ],
            }
        )
        return value

    parent_plan = compile_workflow(
        {
            "document": _document("parent"),
            "do": [
                {
                    "start": {
                        "input": {"from": {"transformed": "${ .value }"}},
                        "run": {
                            "await": False,
                            "workflow": {
                                "namespace": "detached-children",
                                "name": "child",
                                "version": "1.0.0",
                                "input": {"request": "child-only"},
                            },
                        },
                    }
                }
            ],
        }
    )
    first = services()
    engine = engine_type()
    await engine.initialize(first)
    parent = first.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="correlation",
        owner_principal="principal",
        workflow_name=parent_plan.name,
        workflow_version=parent_plan.version,
        workflow_fingerprint=parent_plan.fingerprint,
    )
    try:
        async with asyncio.timeout(20):
            result = await engine.invoke(parent_plan, parent, {"value": "original"})
        assert result.status == "completed", result.error
        assert result.output == {"transformed": "original"}
        assert calls == []
        jobs = first.children.pending_jobs(engine_name)
        jobs = [job for job in jobs if job.invocation_id == parent.invocation_id]
        assert len(jobs) == 1
        decision = jobs[0]
        receipt = first.effect_journal.get(decision)
        child_id = receipt.output["child_id"]
        assert receipt.output["lifetime"] == "independent"
        assert first.invocations.get(child_id).status == "running"
        if reopen:
            await engine.shutdown()
            first.close()
            first = services()
            engine = engine_type()
            await engine.initialize(first)
        dispatcher = ChildDispatcher(first, engine)
        try:
            await dispatcher.tick(job_key=decision.key)
            async with asyncio.timeout(20):
                await entered.wait()
            assert first.invocations.get(parent.invocation_id).status == "completed"
            assert len(calls) == 1
            await dispatcher.tick(job_key=decision.key)
            assert len(calls) == 1
            release.set()
            async with asyncio.timeout(20):
                while dispatcher._workers:
                    await asyncio.sleep(0.005)
            assert not dispatcher._errors, list(dispatcher._errors)
            child = first.invocations.get(child_id)
            assert child.status == "completed"
            assert child.output == {"child_result": "different-from-parent"}
            assert child.parent_invocation_id == parent.invocation_id
            assert child.parent_task_reference == "/do/0/start"
            assert child.user_id == "correlation" and child.owner_principal == "principal"
            assert first.invocations.read_input(child) == {"request": "child-only"}
            assert first.invocations.get(parent.invocation_id).output == {"transformed": "original"}
            assert first.effect_journal.get(decision) == receipt
            await dispatcher.tick(job_key=decision.key)
            assert len(calls) == 1
        finally:
            release.set()
            await dispatcher.stop()
            child = first.invocations.get(child_id)
            if child is not None and child.status in {"running", "waiting"}:
                await engine.cancel(child)
    finally:
        await engine.shutdown()
        first.close()
