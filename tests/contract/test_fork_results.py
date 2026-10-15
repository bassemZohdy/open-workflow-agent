"""Ordered OWS fork outputs, isolated contexts and explicit data aggregation."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def _workflow(tasks):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "ordered-fork",
            "version": "1.0.0",
        },
        "do": tasks,
    }


async def _invoke(services, engine_name, engine_type, tasks, data):
    plan = compile_workflow(_workflow(tasks))
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        result = await engine.invoke(await engine.compile(plan), handle, data)
        assert result.status == "completed", result.error
        return result.output
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_fork_order_is_declaration_order_even_when_completion_order_reverses(
    services, engine_name, engine_type
):
    second_done = asyncio.Event()
    completed = []

    async def call(_protocol, payload):
        branch = payload["body"]["branch"]
        if branch == "first":
            await second_done.wait()
        completed.append(branch)
        if branch == "second":
            second_done.set()
        return {"shared": branch}

    services.call_protocol = call
    output = await asyncio.wait_for(
        _invoke(
            services,
            engine_name,
            engine_type,
            [
                {
                    "parallel": {
                        "fork": {
                            "branches": [
                                {
                                    branch: {
                                        "call": "http",
                                        "with": {
                                            "method": "post",
                                            "endpoint": "https://unused.test/action",
                                            "body": {"branch": branch},
                                        },
                                    }
                                }
                                for branch in ("first", "second")
                            ]
                        }
                    }
                }
            ],
            {"original": "must not be merged"},
        ),
        timeout=10,
    )
    assert completed == ["second", "first"]
    assert output == [{"shared": "first"}, {"shared": "second"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("values", [[None], [None, False, 7, "value", [1, 2], {"key": "value"}]])
async def test_fork_retains_each_branch_json_value(services, engine_name, engine_type, values):
    output = await _invoke(
        services,
        engine_name,
        engine_type,
        [
            {
                "parallel": {
                    "fork": {
                        "branches": [
                            {
                                f"branch{index}": {
                                    "set": {"unused": True},
                                    "output": {"as": "${ " + json.dumps(value) + " }"},
                                }
                            }
                            for index, value in enumerate(values)
                        ]
                    }
                }
            }
        ],
        {"original": True},
    )
    assert output == values


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_nested_fork_preserves_nested_result_arrays(services, engine_name, engine_type):
    output = await _invoke(
        services,
        engine_name,
        engine_type,
        [
            {
                "outer": {
                    "fork": {
                        "branches": [
                            {
                                "inner": {
                                    "fork": {
                                        "branches": [
                                            {"a": {"set": {"shared": "a"}}},
                                            {"b": {"set": {"shared": "b"}}},
                                        ]
                                    }
                                }
                            },
                            {"c": {"set": {"shared": "c"}}},
                        ]
                    }
                }
            }
        ],
        {},
    )
    assert output == [[{"shared": "a"}, {"shared": "b"}], {"shared": "c"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_branch_exports_are_isolated_and_parent_context_is_preserved(
    services, engine_name, engine_type
):
    data = {"original": [1]}
    original = copy.deepcopy(data)
    output = await _invoke(
        services,
        engine_name,
        engine_type,
        [
            {
                "parallel": {
                    "fork": {
                        "branches": [
                            {
                                branch: {
                                    "do": [
                                        {
                                            "change": {
                                                "set": {"owner": branch},
                                                "export": {"as": "${ . }"},
                                            }
                                        },
                                        {"read": {"set": {"context": "${ $context }"}}},
                                    ]
                                }
                            }
                            for branch in ("left", "right")
                        ]
                    }
                }
            },
            {"after": {"set": {"results": "${ . }", "context": "${ $context }"}}},
        ],
        data,
    )
    assert output == {
        "results": [{"context": {"owner": "left"}}, {"context": {"owner": "right"}}],
        "context": original,
    }
    assert data == original


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_fork_pipeline_explicitly_aggregates_results_and_replaces_parent_context(
    services, engine_name, engine_type
):
    output = await _invoke(
        services,
        engine_name,
        engine_type,
        [
            {
                "parallel": {
                    "fork": {
                        "branches": [
                            {"one": {"set": {"amount": 1}}},
                            {"two": {"set": {"amount": 2}}},
                        ]
                    },
                    "output": {"as": "${ map(.amount) | add }"},
                    "export": {"as": "${ {total: $output} }"},
                }
            },
            {"after": {"set": {"value": "${ . }", "context": "${ $context }"}}},
        ],
        {"original": True},
    )
    assert output == {"value": 3, "context": {"total": 3}}


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_object_merge_requires_explicit_fork_output_expression(
    services, engine_name, engine_type
):
    output = await _invoke(
        services,
        engine_name,
        engine_type,
        [
            {
                "parallel": {
                    "fork": {
                        "branches": [
                            {"first": {"set": {"shared": "first", "a": 1}}},
                            {"second": {"set": {"shared": "second", "b": 2}}},
                        ]
                    },
                    "output": {"as": "${ reduce .[] as $branch ($input; . + $branch) }"},
                }
            }
        ],
        {"original": True},
    )
    assert output == {"original": True, "shared": "second", "a": 1, "b": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_failed_fork_drains_sibling_before_returning_common_error(
    services, engine_name, engine_type
):
    sibling_started = asyncio.Event()
    sibling_drained = asyncio.Event()

    async def call(protocol, payload):
        assert protocol == "http"
        if payload["body"]["branch"] == "fail":
            await sibling_started.wait()
            raise WorkflowExecutionError("branch failed", details={"type": "urn:test:branch"})
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            sibling_drained.set()

    services.call_protocol = call
    plan = compile_workflow(
        _workflow(
            [
                {
                    "parallel": {
                        "fork": {
                            "branches": [
                                {
                                    branch: {
                                        "call": "http",
                                        "with": {
                                            "method": "post",
                                            "endpoint": "https://unused.test/action",
                                            "body": {"branch": branch},
                                        },
                                    }
                                }
                                for branch in ("fail", "slow")
                            ]
                        }
                    }
                }
            ]
        )
    )
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=10)
        assert result.status == "faulted"
        assert result.output is None
        assert result.error["code"] == "workflow_execution_error"
        assert result.error["details"]["instance"] == "/do/0/parallel/fork/branches/0/fail"
        assert sibling_drained.is_set()
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_restart_preserves_ordered_fork_output_on_each_adapter(
    tmp_path, engine_name, engine_type
):
    plan = compile_workflow(
        _workflow(
            [
                {
                    "parallel": {
                        "fork": {
                            "branches": [
                                {"first": {"set": {"shared": "first"}}},
                                {
                                    "second": {
                                        "do": [
                                            {"value": {"set": {"shared": "second"}}},
                                            {"pause": {"wait": {"seconds": 300}}},
                                        ]
                                    }
                                },
                            ]
                        }
                    }
                }
            ]
        )
    )
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(await engine.compile(plan), handle, {}))
    try:
        async with asyncio.timeout(20):
            while handle.status != "waiting":
                if running.done():
                    pytest.fail(str(running.result()))
                await asyncio.sleep(0.01)
        waiting = await asyncio.wait_for(asyncio.shield(running), timeout=20)
        assert waiting.status == "waiting", waiting.error
        assert handle.invocation_id not in engine._active
        assert handle.invocation_id not in engine._ownership_tasks
        assert handle._execution_lease is None
        assert services.invocations.read_input(handle) == {}
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    resumed_engine = engine_type()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None and persisted.status == "waiting"
    try:
        result = await asyncio.wait_for(resumed_engine.resume(persisted, {}, plan), timeout=20)
        assert result.status == "completed", result.error
        expected = [{"shared": "first"}, {"shared": "second"}]
        assert result.output == expected
        stored = restarted.invocations.get(handle.invocation_id)
        assert stored is not None and stored.status == "completed" and stored.output == expected
    finally:
        await resumed_engine.shutdown()
        restarted.close()
