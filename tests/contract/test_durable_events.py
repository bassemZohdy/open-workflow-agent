"""Independent portable event semantics and real native recovery acceptance."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from engine_cases import engine_cases
from open_workflow_agent.approvals import (
    APPROVAL_DECISION_EVENT,
    APPROVAL_REQUEST_EVENT,
    ApprovalEventBus,
)
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.durable_events import DurableEventBus
from open_workflow_agent.errors import EventValidationError, InvocationStateError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")

PRINCIPAL_CASES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]


def _services(path: Path, backend: str, model: FakeModel | None = None) -> RuntimeServices:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    return RuntimeServices(
        config, model=model or FakeModel(), database_root=None if datasource else path
    )


def _workflow(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return {
        "document": {"dsl": "1.0.3", "namespace": "events", "name": name, "version": "1.0.0"},
        "do": tasks,
    }


def _handle(
    services: RuntimeServices, engine_name: str, plan: Any, principal: str | None = None
) -> Any:
    return services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
        owner_principal=principal,
    )


def _native(engine: Any, engine_name: str) -> bool:
    return bool(
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    )


async def _publish(
    services: RuntimeServices, subject: str, kind: str, data: Any, index: int
) -> Any:
    return await services.event_bus.publish(
        {
            "id": f"{subject}-{index}",
            "source": "urn:contract:events",
            "type": kind,
            "subject": subject,
            "data": data,
            "tracekey": "preserved",
        },
        default_source="urn:contract:unused",
    )


SEMANTIC_CASES = [
    "one-data",
    "one-envelope",
    "one-raw",
    "one-null",
    "all-arrival-order",
    "all-empty",
    "any-first",
    "any-empty",
    "until-expression",
    "until-one",
    "until-all",
    "until-all-empty",
    "correlation-literal",
    "correlation-expression",
    "correlation-first-bind",
    "correlation-null",
    "filter-expression",
    "filter-regex",
    "foreach-default",
    "foreach-custom",
    "foreach-export",
    "foreach-reusable",
    "foreach-output-schema",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", PRINCIPAL_CASES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("case", SEMANTIC_CASES)
async def test_durable_event_semantics_preserve_order_correlation_and_projection(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str, case: str
) -> None:
    model = FakeModel()
    services = _services(tmp_path, backend, model)
    subject = str(uuid4())
    first = {"with": {"type": "fixture.first", "subject": subject}}
    second = {"with": {"type": "fixture.second", "subject": subject}}
    stop = {"with": {"type": "fixture.stop", "subject": subject}}
    definition: dict[str, Any] = {"listen": {"to": {"one": first}, "read": "data"}}
    events: list[tuple[str, Any]] = [("fixture.first", {"value": 1})]
    expected: Any = [{"value": 1}]
    if case == "one-envelope":
        definition["listen"]["read"] = "envelope"
    elif case == "one-raw":
        definition["listen"]["read"] = "raw"
    elif case == "one-null":
        events = [("fixture.first", None)]
        expected = [None]
    elif case == "all-arrival-order":
        definition["listen"]["to"] = {"all": [first, second]}
        events = [("fixture.second", {"value": 2}), ("fixture.first", {"value": 1})]
        expected = [{"value": 2}, {"value": 1}]
    elif case == "all-empty":
        definition["listen"]["to"] = {"all": []}
        events, expected = [], []
    elif case == "any-first":
        definition["listen"]["to"] = {"any": [first, second]}
        events = [("fixture.second", {"value": 2}), ("fixture.first", {"value": 1})]
        expected = [{"value": 2}]
    elif case == "any-empty":
        # Use a scoped deployment without unrelated retained events in this case.
        # The input event type is deliberately not one of the usual filters.
        definition["listen"]["to"] = {"any": []}
        events = [("fixture.unlisted", {"value": 3})]
        expected = [{"value": 3}]
    elif case == "until-expression":
        definition["listen"]["to"] = {"any": [first], "until": "${ .stop == true }"}
        events = [("fixture.first", {"value": 1}), ("fixture.first", {"stop": True})]
        expected = [{"value": 1}]
    elif case in {"until-one", "until-all"}:
        until = {"one": stop} if case == "until-one" else {"all": [stop, second]}
        definition["listen"]["to"] = {"any": [first], "until": until}
        events = [("fixture.first", {"value": 1}), ("fixture.stop", {"excluded": True})]
        if case == "until-all":
            events.append(("fixture.second", {"excluded": True}))
    elif case == "until-all-empty":
        definition["listen"]["to"] = {"any": [first], "until": {"all": []}}
        events, expected = [], []
    elif case.startswith("correlation-"):
        rules: dict[str, Any] = {"order": {"from": "${ .data.order }"}}
        if case == "correlation-literal":
            rules["order"]["expect"] = "chosen"
        elif case == "correlation-expression":
            rules["order"]["expect"] = "${ .order }"
        elif case == "correlation-null":
            rules["order"]["expect"] = "${ null }"
        first["correlate"] = rules
        second["correlate"] = rules
        if case == "correlation-first-bind":
            definition["listen"]["to"] = {"all": [first, second]}
            events = [
                ("fixture.first", {"order": "chosen", "value": 1}),
                ("fixture.second", {"order": "other", "value": 99}),
                ("fixture.second", {"order": "chosen", "value": 2}),
            ]
            expected = [{"order": "chosen", "value": 1}, {"order": "chosen", "value": 2}]
        else:
            chosen = None if case == "correlation-null" else "chosen"
            events = [
                ("fixture.first", {"order": "other", "value": 99}),
                ("fixture.first", {"order": chosen, "value": 1}),
            ]
            expected = [{"order": chosen, "value": 1}]
    elif case == "filter-expression":
        # Discriminator rejection precedes dynamic predicates even in canonical data-first order.
        first["with"] = {"data": "${ .value > 2 }", "subject": subject, "type": "fixture.first"}
        events = [("fixture.first", {"value": 1}), ("fixture.first", {"value": 3})]
        expected = [{"value": 3}]
    elif case == "filter-regex":
        first["with"]["type"] = r"^fixture\.(first|second)$"
        events = [("fixture.other", {"value": 99}), ("fixture.second", {"value": 2})]
        expected = [{"value": 2}]
    elif case.startswith("foreach-"):
        item, index = ("eventItem", "eventIndex") if case == "foreach-custom" else ("item", "index")
        definition["listen"]["to"] = {"all": [first, second]}
        definition["foreach"] = {
            "do": [
                {
                    "project": {
                        "set": {
                            "value": "${ $" + item + ".value * 10 }",
                            "index": "${ $" + index + " }",
                        }
                    }
                }
            ],
            "output": {"as": "${ {value: .value, index: .index} }"},
        }
        if case == "foreach-custom":
            definition["foreach"].update({"item": item, "at": index})
        elif case == "foreach-export":
            definition["foreach"]["export"] = {
                "as": "${ $context + {seen: (($context.seen // []) + [$item.value])} }"
            }
        elif case == "foreach-reusable":
            definition["foreach"]["do"] = [
                {
                    "retry-frame": {
                        "try": [
                            {
                                "apply": {
                                    "call": "scale",
                                    "with": {
                                        "value": "${ $item.value }",
                                        "index": "${ $index }",
                                    },
                                }
                            }
                        ],
                        "catch": {"retry": "once"},
                    }
                }
            ]
        elif case == "foreach-output-schema":
            definition["foreach"]["output"]["schema"] = {"document": {"type": "integer"}}
        events = [("fixture.first", {"value": 1}), ("fixture.second", {"value": 2})]
        expected = [{"value": 10, "index": 0}, {"value": 20, "index": 1}]
    engine = engine_type()
    try:
        await engine.initialize(services)
        tasks = [{"listen": definition}]
        if case == "foreach-export":
            tasks.append({"result": {"set": {"seen": "${ $context.seen }"}}})
            expected = {"seen": [1, 2]}
        source = _workflow(tasks, "event-semantics")
        if case == "foreach-reusable":
            source["use"] = {
                "functions": {
                    "scale": {
                        "timeout": "bounded",
                        "set": {
                            "value": "${ .value * 10 }",
                            "index": "${ .index }",
                        },
                    }
                },
                "timeouts": {"bounded": {"after": {"seconds": 30}}},
                "retries": {"once": {"limit": {"attempt": {"count": 1}}}},
            }
        plan = compile_workflow(source)
        assert services.event_bus.durable, "default embedded event delivery must be durable"
        principal = subject if case == "any-empty" else None
        handle = _handle(services, engine_name, plan, principal)
        envelopes = []
        if case == "filter-expression":
            # Scalar payloads would fault .value if evaluated before exclusion.
            await _publish(services, str(uuid4()), "fixture.first", 1, -2)
            await _publish(services, subject, "fixture.unrelated", 1, -1)
        for event_ordinal, (kind, data) in enumerate(events):
            if principal is None:
                envelopes.append(await _publish(services, subject, kind, data, event_ordinal))
            else:
                bus = services.event_bus
                assert isinstance(bus, DurableEventBus)
                envelopes.append(
                    await bus.publish_scoped(
                        {
                            "id": f"{subject}-{event_ordinal}",
                            "source": "urn:contract:events",
                            "type": kind,
                            "subject": subject,
                            "data": data,
                            "tracekey": "preserved",
                        },
                        default_source="urn:contract:unused",
                        scope=principal,
                    )
                )
        result = await asyncio.wait_for(
            engine.invoke(plan, handle, {"order": "chosen"}),
            timeout=30,
        )
        if case == "foreach-output-schema":
            assert result.status == "faulted" and result.error
            assert result.error["code"] == "workflow_semantic_error"
            assert result.error["details"]["validator"] == "type"
            assert result.error["details"]["type"] == (
                "https://serverlessworkflow.io/spec/1.0.0/errors/validation"
            )
            assert result.error["details"]["status"] == 400
            assert not model.calls
            return
        assert result.status == "completed", result.error
        assert not model.calls
        if case == "one-envelope":
            assert result.output == [envelopes[0].as_dict()]
            assert result.output[0]["tracekey"] == "preserved"
        elif case == "one-raw":
            assert isinstance(result.output, list) and len(result.output) == 1
            assert json.loads(result.output[0]) == envelopes[0].as_dict()
        else:
            assert result.output == expected
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", PRINCIPAL_CASES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_durable_repeated_listener_occurrences_do_not_reconsume_history(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str
) -> None:
    services = _services(tmp_path, backend)
    subject = str(uuid4())
    plan = compile_workflow(
        _workflow(
            [
                {
                    "loop": {
                        "for": {"each": "turn", "in": "${ [0,1] }"},
                        "do": [
                            {
                                "next": {
                                    "listen": {
                                        "to": {
                                            "one": {
                                                "with": {"type": "fixture.next", "subject": subject}
                                            }
                                        }
                                    },
                                    "export": {
                                        "as": (
                                            "${ $context + "
                                            "{received: (($context.received // []) + .)} }"
                                        )
                                    },
                                }
                            }
                        ],
                    }
                },
                {"result": {"set": {"received": "${ $context.received }"}}},
            ],
            "event-loop",
        )
    )
    engine = engine_type()
    try:
        await engine.initialize(services)
        await _publish(services, subject, "fixture.next", 1, 1)
        await _publish(services, subject, "fixture.next", 2, 2)
        result = await asyncio.wait_for(
            engine.invoke(plan, _handle(services, engine_name, plan), {}), timeout=30
        )
        assert result.status == "completed", result.error
        assert result.output == {"received": [1, 2]}
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", PRINCIPAL_CASES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "boundary", ["waiting", "selected", "partial-all", "changed-selection", "missing-subscription"]
)
async def test_durable_event_native_restart_preserves_selection_and_admission(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str, boundary: str
) -> None:
    subject = str(uuid4())
    model = FakeModel({"done": True})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    if not _native(engine, engine_name):
        services.close()
        pytest.skip("independent native engine environment is required")
    filters = [
        {
            "with": {"type": kind, "subject": subject},
            "correlate": {"order": {"from": "${ .data.order }"}},
        }
        for kind in ("fixture.first", "fixture.second")
    ]
    strategy = {"all": filters} if boundary == "partial-all" else {"one": filters[0]}
    plan = compile_workflow(
        _workflow(
            [
                {"listen": {"listen": {"to": strategy}}},
                {"action": {"call": "llm:1.0.0@default", "with": {"prompt": "continue"}}},
            ],
            "durable-restart",
        )
    )
    await engine.initialize(services)
    store = services.event_store
    assert store is not None
    handle = _handle(services, engine_name, plan)
    reached = asyncio.Event()
    original_listen = engine.executor._run_listen

    async def after_selection(*args: Any, **kwargs: Any) -> Any:
        value = await original_listen(*args, **kwargs)
        if boundary in {"selected", "changed-selection", "missing-subscription"}:
            reached.set()
            await asyncio.Event().wait()
        return value

    engine.executor._run_listen = after_selection
    task = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        async with asyncio.timeout(30):
            while handle.status != "waiting":
                if task.done():
                    raise AssertionError(await task)
                await asyncio.sleep(0.01)
        if boundary != "waiting":
            await _publish(services, subject, "fixture.first", {"order": "chosen", "value": 1}, 1)
        if boundary == "partial-all":
            async with asyncio.timeout(30):
                while not store.connection.execute(
                    "SELECT s.ordinal FROM event_selections s "
                    "JOIN event_subscriptions r ON r.identity = s.subscription "
                    "WHERE r.invocation_id = ?",
                    (handle.invocation_id,),
                ).fetchall():
                    if task.done():
                        raise AssertionError(await task)
                    await asyncio.sleep(0.01)
        elif boundary != "waiting":
            await asyncio.wait_for(reached.wait(), timeout=30)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=10)
        assert not model.calls
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.shutdown()
        services.close()
    restarted = _services(tmp_path, backend, model)
    resumed = engine_type()
    await resumed.initialize(restarted)
    restarted_store = restarted.event_store
    assert restarted_store is not None
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None
    before = persisted._persisted_payload
    try:
        if boundary == "waiting":
            await _publish(restarted, subject, "fixture.first", {"order": "chosen", "value": 1}, 1)
        elif boundary == "partial-all":
            await _publish(restarted, subject, "fixture.second", {"order": "other", "value": 99}, 2)
            await _publish(restarted, subject, "fixture.second", {"order": "chosen", "value": 2}, 3)
        elif boundary == "changed-selection":
            restarted_store.connection.execute(
                "UPDATE event_selections SET digest = ? WHERE subscription IN "
                "(SELECT identity FROM event_subscriptions WHERE invocation_id = ?)",
                ("changed", handle.invocation_id),
            )
            restarted_store.connection.commit()
        elif boundary == "missing-subscription":
            restarted_store.connection.execute(
                "DELETE FROM event_subscriptions WHERE invocation_id = ?", (handle.invocation_id,)
            )
            restarted_store.connection.commit()
        if boundary in {"changed-selection", "missing-subscription"}:
            with pytest.raises(InvocationStateError):
                await resumed.resume(persisted, None, plan)
            saved = restarted.invocations.get(handle.invocation_id)
            assert saved is not None and saved._persisted_payload == before
            assert not model.calls
        else:
            result = await asyncio.wait_for(resumed.resume(persisted, None, plan), timeout=30)
            assert result.status == "completed", result.error
            assert result.output == {"done": True}
            assert len(model.calls) == 1
    finally:
        await resumed.shutdown()
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", PRINCIPAL_CASES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_principal_scoped_events_cannot_resume_a_different_owner(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str
) -> None:
    services = _services(tmp_path, backend)
    bus = services.event_bus
    assert isinstance(bus, DurableEventBus)
    subject, alpha, beta = str(uuid4()), "alpha-" + str(uuid4()), "beta-" + str(uuid4())
    plan = compile_workflow(
        _workflow(
            [
                {
                    "listen": {
                        "listen": {
                            "to": {"one": {"with": {"type": "fixture.scoped", "subject": subject}}}
                        }
                    }
                }
            ],
            "durable-scoped",
        )
    )
    engine = engine_type()
    await engine.initialize(services)
    first, second = (
        _handle(services, engine_name, plan, alpha),
        _handle(services, engine_name, plan, beta),
    )
    tasks = [asyncio.create_task(engine.invoke(plan, handle, {})) for handle in (first, second)]
    properties = {
        "id": subject,
        "source": "urn:contract:scoped",
        "type": "fixture.scoped",
        "subject": subject,
        "data": "alpha",
        "principal": beta,
    }
    try:
        async with asyncio.timeout(30):
            while first.status != "waiting" or second.status != "waiting":
                if any(task.done() for task in tasks):
                    raise AssertionError([task.result() for task in tasks if task.done()])
                await asyncio.sleep(0.01)
        await bus.publish_scoped(properties, default_source="urn:unused", scope=alpha)
        result = await asyncio.wait_for(tasks[0], timeout=30)
        assert result.status == "completed" and result.output == ["alpha"], result.error
        assert not tasks[1].done()
        await bus.publish_scoped(
            {**properties, "data": "beta"}, default_source="urn:unused", scope=beta
        )
        result = await asyncio.wait_for(tasks[1], timeout=30)
        assert result.status == "completed" and result.output == ["beta"], result.error
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", PRINCIPAL_CASES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "damage",
    [
        "clean-listen",
        "clean-emit",
        "subscription",
        "selection",
        "inbox",
        "outbox",
        "all-event-records",
    ],
)
async def test_parent_recovery_reconciles_completed_child_event_dependencies(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str, damage: str
) -> None:
    subject = str(uuid4())
    model = FakeModel({"done": True})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    if not _native(engine, engine_name):
        services.close()
        pytest.skip("independent native engine environment is required")
    emit = damage in {"clean-emit", "outbox"}
    event_task = (
        {
            "emit": {
                "event": {"with": {"id": subject, "type": "fixture.child", "data": {"child": True}}}
            }
        }
        if emit
        else {"listen": {"to": {"one": {"with": {"subject": subject, "type": "fixture.child"}}}}}
    )
    child = _workflow(
        [
            {"event": event_task},
            {"child-action": {"call": "llm:1.0.0@default", "with": {"prompt": "child once"}}},
        ],
        "durable-child",
    )
    services.workflow_catalog.register(child)
    plan = compile_workflow(
        _workflow(
            [
                {
                    "child": {
                        "run": {
                            "workflow": {
                                "namespace": "events",
                                "name": "durable-child",
                                "version": "1.0.0",
                                "input": {},
                            }
                        }
                    }
                },
                {"pause": {"wait": {"seconds": 300}}},
                {
                    "parent-action": {
                        "call": "llm:1.0.0@default",
                        "with": {"prompt": "parent after wait"},
                    }
                },
            ],
            "durable-parent",
        )
    )
    await engine.initialize(services)
    if not emit:
        await _publish(services, subject, "fixture.child", {"child": True}, 1)
    handle = _handle(services, engine_name, plan)
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    child_id: str | None = None
    try:
        waiting = await asyncio.wait_for(asyncio.shield(running), timeout=30)
        assert waiting.status == "waiting", waiting.error
        assert handle.invocation_id not in engine._active
        assert handle.invocation_id not in engine._ownership_tasks
        assert handle._execution_lease is None
        assert services.invocations.read_input(handle) == {}
        rows = services.effect_journal.connection.execute(
            "SELECT i.payload, e.payload FROM workflow_effect_invocations i "
            "JOIN workflow_effects e ON e.identity = i.identity WHERE i.invocation_id = ?",
            (handle.invocation_id,),
        ).fetchall()
        child_receipts = [
            json.loads(payload)
            for identity, payload in rows
            if json.loads(identity)["operation"] == "run:workflow"
        ]
        assert len(child_receipts) == 1
        child_id = child_receipts[0]["child_invocation"]["invocation_id"]
        assert len(model.calls) == 1
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()
    assert child_id is not None
    restarted = _services(tmp_path, backend, model)
    restarted.workflow_catalog.register(child)
    resumed = engine_type()
    await resumed.initialize(restarted)
    store = restarted.event_store
    persisted = restarted.invocations.get(handle.invocation_id)
    assert store is not None and persisted is not None
    original_parent = persisted._persisted_payload
    try:
        if damage == "subscription":
            store.connection.execute(
                "DELETE FROM event_subscriptions WHERE invocation_id = ?", (child_id,)
            )
        elif damage == "selection":
            store.connection.execute(
                "DELETE FROM event_selections WHERE subscription IN "
                "(SELECT identity FROM event_subscriptions WHERE invocation_id = ?)",
                (child_id,),
            )
        elif damage == "inbox":
            store.connection.execute(
                "DELETE FROM event_inbox WHERE sequence IN (SELECT s.inbox_sequence "
                "FROM event_selections s JOIN event_subscriptions r "
                "ON r.identity = s.subscription WHERE r.invocation_id = ?)",
                (child_id,),
            )
        elif damage == "outbox":
            store.connection.execute(
                "DELETE FROM event_outbox WHERE invocation_id = ?", (child_id,)
            )
        elif damage == "all-event-records":
            store.connection.execute(
                "DELETE FROM event_selections WHERE subscription IN "
                "(SELECT identity FROM event_subscriptions WHERE invocation_id = ?)",
                (child_id,),
            )
            store.connection.execute(
                "DELETE FROM event_subscriptions WHERE invocation_id = ?", (child_id,)
            )
            store.connection.execute(
                "DELETE FROM event_outbox WHERE invocation_id = ?", (child_id,)
            )
            store.connection.execute(
                "DELETE FROM event_invocation_profiles WHERE invocation_id = ?", (child_id,)
            )
        store.connection.commit()
        if damage.startswith("clean-"):
            result = await asyncio.wait_for(
                resumed.resume(persisted, {"continue": True}, plan), timeout=30
            )
            assert result.status == "completed", result.error
            assert result.output == {"done": True}
            assert len(model.calls) == 2
        else:
            with pytest.raises(InvocationStateError):
                await resumed.resume(persisted, {"continue": True}, plan)
            saved = restarted.invocations.get(handle.invocation_id)
            assert saved is not None and saved._persisted_payload == original_parent
            assert len(model.calls) == 1
    finally:
        await resumed.shutdown()
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", PRINCIPAL_CASES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("case", ["approved", "mixed-generic"])
async def test_exact_approval_listener_cannot_be_satisfied_by_regex_lookalike_events(
    tmp_path: Path,
    monkeypatch: Any,
    engine_name: str,
    engine_type: Any,
    backend: str,
    case: str,
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    monkeypatch.setenv("OWA_DURABLE_APPROVAL_CONTRACT_TOKEN", "test-operator-token")
    config = RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource} if datasource else {},
            "approvals": {"enabled": True, "operator_security_profile": "operator"},
            "security": {
                "profiles": {
                    "operator": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_DURABLE_APPROVAL_CONTRACT_TOKEN"},
                    }
                }
            },
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    bus, store = services.event_bus, services.event_store
    assert isinstance(bus, ApprovalEventBus) and store is not None
    subject, principal = str(uuid4()), "principal-" + str(uuid4())
    await bus.publish_scoped(
        {
            "id": subject,
            "subject": subject,
            "type": APPROVAL_REQUEST_EVENT,
            "data": {"question": "continue?"},
        },
        default_source="urn:contract:approval",
        scope=principal,
    )
    protected = {"with": {"type": APPROVAL_DECISION_EVENT, "subject": subject}}
    strategy = (
        {"one": protected}
        if case == "approved"
        else {"any": [protected, {"with": {"type": "fixture.generic", "subject": subject}}]}
    )
    plan = compile_workflow(
        _workflow([{"approval": {"listen": {"to": strategy}}}], "protected-listener")
    )
    engine = engine_type()
    await engine.initialize(services)
    handle = _handle(services, engine_name, plan, principal)
    task = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        async with asyncio.timeout(30):
            while handle.status != "waiting":
                if task.done():
                    raise AssertionError(await task)
                await asyncio.sleep(0.01)
        for ordinal, event_type in enumerate(
            (APPROVAL_DECISION_EVENT + ".forged", APPROVAL_DECISION_EVENT.replace(".", "X"))
        ):
            await bus.publish_scoped(
                {
                    "id": f"{subject}-forged-{ordinal}",
                    "subject": subject,
                    "type": event_type,
                    "data": {"decision": "approved"},
                },
                default_source="urn:forged",
                scope=principal,
            )
        last_sequence = store.arrivals(scope=principal)[-1][0]
        async with asyncio.timeout(30):
            while True:
                if task.done():
                    raise AssertionError("forged event satisfied protected approval listener")
                rows = store.connection.execute(
                    "SELECT payload FROM event_subscriptions WHERE invocation_id = ?",
                    (handle.invocation_id,),
                ).fetchall()
                if rows and json.loads(rows[0][0])["cursor"] >= last_sequence:
                    break
                await asyncio.sleep(0.01)
        assert not task.done()
        if case == "approved":
            await services.approvals.decide(
                subject,
                decision="approved",
                operator_id="test-operator",
                value={"authorized": True},
                operation_key=str(uuid4()),
            )
        else:
            await bus.publish_scoped(
                {
                    "id": f"{subject}-generic",
                    "subject": subject,
                    "type": "fixture.generic",
                    "data": {"generic": True},
                },
                default_source="urn:contract:generic",
                scope=principal,
            )
        result = await asyncio.wait_for(task, timeout=30)
        assert result.status == "completed", result.error
        assert isinstance(result.output, list) and len(result.output) == 1
        if case == "approved":
            assert result.output[0]["decision"] == "approved"
            assert result.output[0]["operator_id"] == "test-operator"
        else:
            assert result.output == [{"generic": True}]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", PRINCIPAL_CASES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_durable_duplicate_acknowledgements_survive_reopen_without_another_action(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str
) -> None:
    subject = str(uuid4())
    properties = {
        "id": subject,
        "source": "urn:contract:dedupe",
        "type": "fixture.unique",
        "subject": subject,
        "data": None,
    }
    services = _services(tmp_path, backend)
    first = await services.event_bus.publish(properties, default_source="urn:unused")
    duplicate = await services.event_bus.publish(properties, default_source="urn:unused")
    assert duplicate.as_dict() == first.as_dict()
    services.close()
    restarted = _services(tmp_path, backend)
    engine = engine_type()
    try:
        await engine.initialize(restarted)
        replayed = await restarted.event_bus.publish(properties, default_source="urn:unused")
        assert replayed.as_dict() == first.as_dict()
        with pytest.raises(EventValidationError) as conflict:
            await restarted.event_bus.publish(
                {**properties, "data": "secret-conflict-sentinel"}, default_source="urn:unused"
            )
        assert "secret-conflict-sentinel" not in str(conflict.value)
        alternate = await restarted.event_bus.publish(
            {**properties, "source": "urn:contract:distinct", "data": {"other": True}},
            default_source="urn:unused",
        )
        assert alternate.source != first.source and alternate.id == first.id
        plan = compile_workflow(
            _workflow(
                [
                    {
                        "receive": {
                            "listen": {
                                "to": {
                                    "all": [
                                        {
                                            "with": {
                                                "type": "fixture.unique",
                                                "subject": subject,
                                                "source": "urn:contract:dedupe",
                                            }
                                        },
                                        {
                                            "with": {
                                                "type": "fixture.unique",
                                                "subject": subject,
                                                "source": "urn:contract:distinct",
                                            }
                                        },
                                    ]
                                }
                            }
                        }
                    },
                ],
                "durable-duplicate",
            )
        )
        result = await asyncio.wait_for(
            engine.invoke(plan, _handle(restarted, engine_name, plan), {}), timeout=30
        )
        assert result.status == "completed", result.error
        assert result.output == [None, {"other": True}]
    finally:
        await engine.shutdown()
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", PRINCIPAL_CASES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_late_event_does_not_resume_an_invocation_after_authorized_cancellation(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str
) -> None:
    subject = str(uuid4())
    model = FakeModel({"unexpected": True})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    await engine.initialize(services)
    plan = compile_workflow(
        _workflow(
            [
                {
                    "listen": {
                        "listen": {
                            "to": {"one": {"with": {"subject": subject, "type": "fixture.late"}}}
                        }
                    }
                },
                {
                    "action": {
                        "call": "llm:1.0.0@default",
                        "with": {"prompt": "forbidden after cancel"},
                    }
                },
            ],
            "durable-late",
        )
    )
    handle = _handle(services, engine_name, plan)
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        async with asyncio.timeout(30):
            while handle.status != "waiting":
                if running.done():
                    raise AssertionError(await running)
                await asyncio.sleep(0.01)
        cancelled = await engine.cancel(handle)
        assert cancelled.status == "cancelled"
        assert (await asyncio.wait_for(running, timeout=15)).status == "cancelled"
        await _publish(services, subject, "fixture.late", {"late": True}, 1)
        assert not model.calls
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()
    restarted = _services(tmp_path, backend, model)
    resumed = engine_type()
    await resumed.initialize(restarted)
    try:
        persisted = restarted.invocations.get(handle.invocation_id)
        assert persisted is not None and persisted.status == "cancelled"
        result = await resumed.resume(persisted, None, plan)
        assert result.status == "cancelled"
        assert not model.calls
    finally:
        await resumed.shutdown()
        restarted.close()
