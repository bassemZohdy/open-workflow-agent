"""Portable OWS error fields and ordered lexical catch handling."""

from __future__ import annotations

from typing import Any

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.workflow import compile_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize(
    "case", ["inner", "outer", "handler-fault", "predicate-fault", "timeout-type"]
)
async def test_exception_types_and_scopes_preserve_catch_order(
    services: Any, engine_name: str, engine_type: Any, case: str
) -> None:
    model = FakeModel({"done": True}, failures=1)
    services.model = model
    inner: dict[str, Any] = {
        "try": [
            {"first": {"set": {"stage": "first"}}},
            {"action": {"call": "llm:1.0.0@default", "with": {"prompt": "once"}}},
        ],
        "catch": {
            "as": "problem",
            "errors": {
                "with": {
                    "type": "https://serverlessworkflow.io/spec/1.0.0/errors/communication",
                    "status": 500,
                    "instance": "/do/0/outer/try/0/inner/try/1/action",
                }
            },
            "do": [{"handled": {"set": {"handler": "inner", "status": "${ $problem.status }"}}}],
        },
    }
    outer: dict[str, Any] = {
        "try": [{"inner": inner}],
        "catch": {
            "as": "outerProblem",
            "do": [
                {"handled": {"set": {"handler": "outer", "status": "${ $outerProblem.status }"}}}
            ],
        },
    }
    expected: Any = {"handler": "inner", "status": 500}
    if case == "outer":
        inner["catch"]["exceptWhen"] = "${ $problem.status == 500 }"
        outer["catch"]["errors"] = {
            "with": {
                "type": "https://serverlessworkflow.io/spec/1.0.0/errors/communication",
                "status": 500,
            }
        }
        expected = {"handler": "outer", "status": 500}
    elif case == "handler-fault":
        inner["catch"]["do"] = [
            {
                "rethrow": {
                    "raise": {
                        "error": {
                            "type": "urn:test:handler",
                            "status": 409,
                            "detail": "handler failed",
                        }
                    }
                }
            }
        ]
        outer["catch"]["errors"] = {
            "with": {
                "type": "urn:test:handler",
                "status": 409,
                "instance": "/do/0/outer/try/0/inner/catch/do/0/rethrow",
            }
        }
        expected = {"handler": "outer", "status": 409}
    elif case == "predicate-fault":
        inner["catch"]["when"] = '${ error("predicate failed") }'
        outer["catch"]["errors"] = {
            "with": {"type": "https://serverlessworkflow.io/spec/1.0.0/errors/expression"}
        }
        expected = {"handler": "outer", "status": 400}
    elif case == "timeout-type":
        inner["try"] = [
            {"pause": {"wait": {"seconds": 10}, "timeout": {"after": {"milliseconds": 0}}}}
        ]
        inner["catch"]["errors"] = {
            "with": {
                "type": "https://serverlessworkflow.io/spec/1.0.0/errors/timeout",
                "status": 408,
                "instance": "/do/0/outer/try/0/inner/try/0/pause",
            }
        }
        expected = {"handler": "inner", "status": 408}
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "scoped-errors",
                "version": "1.0.0",
            },
            "do": [{"outer": outer}],
        }
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
        result = await engine.invoke(plan, handle, {"original": True})
        assert result.status == "completed", result.error
        assert result.output == expected
        assert len(model.calls) == (0 if case == "timeout-type" else 1)
    finally:
        await engine.shutdown()
