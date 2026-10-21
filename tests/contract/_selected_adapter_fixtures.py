"""Independently authored canonical compiler and embedded transport fixtures."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.services import RuntimeServices
from test_execution_authority_isolation import (
    actual_native,
    authority,
    configuration,
    create,
)

ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
RAW = {"payload": {"value": 4}, "untouched": None}


def runtime(path, backend, model=None, *, bounded_memory=False):
    config = configuration(backend)
    if bounded_memory:
        values = config.model_dump(mode="json", exclude_unset=True)
        values["memory"] = {"enabled": True, "bounds": {"enabled": True}}
        values["agent"] = {"tools": ["add_memory"]}
        config = RuntimeConfig.model_validate(values)
    services = RuntimeServices(
        config,
        model=model or FakeModel(),
        database_root=None if backend == "postgresql" else path,
        embedded_event_transport=True,
        recovery_profile_provider=lambda engine: {
            "id": "selected-adapters-fixture",
            "version": "1",
        },
    )
    assert services.event_transport is not None
    assert (
        services.effect_journal.connection.kind == services.invocations.connection.kind == backend
    )
    if backend == "postgresql":
        assert services.database_root is None
        assert services.engine_database_path("adk", create_namespace=False).startswith("postgres")
        assert services.engine_database_path("langgraph", create_namespace=False).startswith(
            "postgres"
        )
    return services


def definition(tasks, *, name=None, **changes):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "selected-adapters",
            "name": name or uuid4().hex,
            "version": "1.0.0",
        },
        "timeout": {"after": {"seconds": 120}},
        "do": tasks,
        **changes,
    }


async def invoke(engine, services, plan, handle, data=RAW):
    with authority_scope(authority(services, "alice")):
        return await engine.invoke(plan, handle, copy.deepcopy(data))


def message(services, subject, *, index=1, data=None, **changes):
    return services.event_transport.codec.capture(
        {
            "specversion": "1.0",
            "id": subject + "-" + str(index),
            "source": "urn:contract:selected-adapters",
            "type": "selected.accepted",
            "time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "subject": subject,
            "traceparent": "exact-correlation",
            "count": index,
            "data": data,
            **changes,
        }
    )


def admit(services, wire):
    with authority_scope(authority(services, "alice")):
        return services.event_transport.admit(wire.encoded, scope="alice")


def receipts(services, handle):
    rows = services.effect_journal.connection.execute(
        "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=? ORDER BY identity",
        (handle.invocation_id,),
    ).fetchall()
    return [
        (identity, services.effect_journal.get(identity))
        for row in rows
        if (identity := EffectIdentity.from_value(json.loads(row[0])))
    ]


def known(services, handle):
    return {
        identity.key: json.loads(
            json.dumps(asdict(ReceiptDependency.capture(identity, receipt)), allow_nan=False)
        )
        for identity, receipt in receipts(services, handle)
        if receipt.status != "pending"
    }


def cases(case):
    if case == "transforms":
        return (
            definition(
                [
                    {
                        "transform": {
                            "input": {
                                "from": "${ .value }",
                                "schema": {
                                    "format": "json",
                                    "document": {
                                        "type": "object",
                                        "required": ["value"],
                                        "properties": {"value": {"type": "integer"}},
                                    },
                                },
                            },
                            "set": {"doubled": "${ . * 2 }"},
                            "output": {
                                "as": {"answer": "${ .doubled }", "raw": "${ $task.input }"}
                            },
                            "export": {"as": {"published": "${ $output.answer }"}},
                        }
                    },
                    {
                        "inspect": {
                            "set": {
                                "answer": "${ .answer }",
                                "raw": "${ .raw }",
                                "context": "${ $context }",
                                "original": "${ $workflow.input }",
                                "reference": "${ $task.reference }",
                            }
                        }
                    },
                ],
                input={"from": "${ .payload }"},
            ),
            {
                "answer": 8,
                "raw": {"value": 4},
                "context": {"published": 8},
                "original": RAW,
                "reference": "/do/1/inspect",
            },
            ["/do/0/transform", "/do/1/inspect"],
        )
    if case == "escaped-nested":
        reference = "/do/0/outer~1name/do/0/inner~0name"
        return (
            definition(
                [
                    {
                        "outer/name": {
                            "do": [
                                {
                                    "inner~name": {
                                        "set": {
                                            "reference": "${ $task.reference }",
                                            "input": "${ $workflow.input }",
                                        }
                                    }
                                }
                            ]
                        }
                    }
                ]
            ),
            {"reference": reference, "input": RAW},
            [reference],
        )
    if case == "reusable":
        reference = "/use/functions/inc~1name"
        return (
            definition(
                [
                    {"first": {"call": "inc/name", "with": {"value": 1}}},
                    {"second": {"call": "inc/name"}},
                ],
                use={
                    "functions": {
                        "inc/name": {
                            "input": {"from": "${ .value }"},
                            "set": {"value": "${ . + 1 }", "source": "${ $task.reference }"},
                        }
                    }
                },
            ),
            {"value": 3, "source": reference},
            [reference, reference],
        )
    if case == "fork-order":
        return (
            definition(
                [
                    {
                        "parallel": {
                            "fork": {
                                "branches": [
                                    {"left": {"set": {"side": "left", "value": None}}},
                                    {"right": {"set": {"side": "right", "value": False}}},
                                ]
                            }
                        }
                    }
                ]
            ),
            [{"side": "left", "value": None}, {"side": "right", "value": False}],
            ["/do/0/parallel/fork/branches/0/left", "/do/0/parallel/fork/branches/1/right"],
        )
    if case == "retry-null":
        return (
            definition(
                [
                    {
                        "controlled": {
                            "try": [
                                {"model": {"call": "llm:1.0.0@default", "with": {"prompt": "once"}}}
                            ],
                            "catch": {"retry": {"limit": {"attempt": {"count": 1}}}},
                        }
                    }
                ]
            ),
            None,
            ["/do/0/controlled/try/0/model"],
        )
    raise AssertionError(case)


__all__ = [
    "ENGINES",
    "RAW",
    "actual_native",
    "admit",
    "authority",
    "cases",
    "create",
    "definition",
    "invoke",
    "known",
    "message",
    "receipts",
    "runtime",
]
