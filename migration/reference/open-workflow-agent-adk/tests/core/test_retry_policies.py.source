"""Spec retry policies and catch error filters."""

import time

import pytest

from openworkflow_adk import load, run_workflow


def _document(tasks: list[dict], **extra: object):
    base: dict = {
        "document": {"dsl": "1.0.3", "namespace": "demo", "name": "retry", "version": "1.0.0"},
        "do": tasks,
    }
    base.update(extra)
    return load(base)


def _flaky_function(calls: list[int], failures: int):
    async def flaky(value: str) -> str:
        calls.append(1)
        if len(calls) <= failures:
            raise RuntimeError("temporary failure")
        return value

    return flaky


async def test_inline_retry_recovers_before_limit() -> None:
    calls: list[int] = []
    document = _document(
        [
            {
                "guarded": {
                    "try": [
                        {
                            "callFlaky": {
                                "call": "flaky",
                                "with": {"value": "recovered"},
                            }
                        }
                    ],
                    "catch": {
                        "retry": {
                            "delay": {"milliseconds": 1},
                            "backoff": {"constant": {}},
                            "limit": {"attempt": {"count": 3}},
                        },
                        "do": [{"fallback": {"set": {"used_fallback": '"yes"'}}}],
                    },
                }
            }
        ]
    )

    events = await run_workflow(document, function_registry={"flaky": _flaky_function(calls, 2)})

    assert len(calls) == 3  # initial attempt + 2 retries
    deltas = {
        k: v
        for event in events
        if event.actions
        for k, v in (event.actions.state_delta or {}).items()
    }
    assert "used_fallback" not in deltas
    assert any(event.output == "recovered" for event in events)


async def test_inline_retry_exhausts_and_runs_catch() -> None:
    calls: list[int] = []
    document = _document(
        [
            {
                "guarded": {
                    "try": [
                        {
                            "callFlaky": {
                                "call": "flaky",
                                "with": {"value": "never"},
                            }
                        }
                    ],
                    "catch": {
                        "as": "failure",
                        "retry": {
                            "delay": {"milliseconds": 1},
                            "limit": {"attempt": {"count": 2}},
                        },
                        "do": [{"fallback": {"set": {"used_fallback": '"yes"'}}}],
                    },
                }
            }
        ]
    )

    events = await run_workflow(document, function_registry={"flaky": _flaky_function(calls, 99)})

    assert len(calls) == 2  # initial attempt + 1 retry
    deltas = {
        k: v
        for event in events
        if event.actions
        for k, v in (event.actions.state_delta or {}).items()
    }
    assert deltas.get("used_fallback") == "yes"
    assert deltas.get("failure", {}).get("title") == "RuntimeError"


async def test_reusable_retry_policy_from_use() -> None:
    calls: list[int] = []
    document = _document(
        [
            {
                "guarded": {
                    "try": [
                        {
                            "callFlaky": {
                                "call": "flaky",
                                "with": {"value": "ok"},
                            }
                        }
                    ],
                    "catch": {
                        "retry": "flakyPolicy",
                        "do": [{"fallback": {"set": {"used_fallback": '"yes"'}}}],
                    },
                }
            }
        ],
        use={
            "retries": {
                "flakyPolicy": {"delay": {"milliseconds": 1}, "limit": {"attempt": {"count": 5}}}
            }
        },
    )

    events = await run_workflow(document, function_registry={"flaky": _flaky_function(calls, 1)})

    assert len(calls) == 2
    deltas = {
        k: v
        for event in events
        if event.actions
        for k, v in (event.actions.state_delta or {}).items()
    }
    assert "used_fallback" not in deltas


async def test_unknown_retry_reference_is_rejected() -> None:
    document = _document(
        [
            {
                "guarded": {
                    "try": [
                        {
                            "boom": {
                                "raise": {
                                    "error": {
                                        "type": "https://demo.test/boom",
                                        "status": 500,
                                        "title": "Boom",
                                    }
                                }
                            }
                        }
                    ],
                    "catch": {
                        "retry": "missing",
                        "do": [{"fallback": {"set": {"used_fallback": '"yes"'}}}],
                    },
                }
            }
        ]
    )

    with pytest.raises(ValueError, match="missing"):
        await run_workflow(document)


async def test_error_filter_mismatch_propagates() -> None:
    document = _document(
        [
            {
                "guarded": {
                    "try": [
                        {
                            "boom": {
                                "raise": {
                                    "error": {"type": "https://demo.test/boom", "status": 500}
                                }
                            }
                        }
                    ],
                    "catch": {
                        "errors": {"with": {"status": 404}},
                        "do": [{"fallback": {"set": {"used_fallback": '"yes"'}}}],
                    },
                }
            }
        ]
    )

    with pytest.raises(Exception):
        await run_workflow(document)


async def test_error_filter_match_runs_catch() -> None:
    document = _document(
        [
            {
                "guarded": {
                    "try": [
                        {
                            "boom": {
                                "raise": {
                                    "error": {"type": "https://demo.test/boom", "status": 503}
                                }
                            }
                        }
                    ],
                    "catch": {
                        "errors": {"with": {"type": "https://demo.test/boom", "status": 503}},
                        "as": "failure",
                        "do": [{"fallback": {"set": {"used_fallback": '"yes"'}}}],
                    },
                }
            }
        ]
    )

    events = await run_workflow(document)

    deltas = {
        k: v
        for event in events
        if event.actions
        for k, v in (event.actions.state_delta or {}).items()
    }
    assert deltas.get("used_fallback") == "yes"
    assert deltas.get("failure", {}).get("status") == 503


async def test_error_filter_accepts_detail_alias() -> None:
    document = _document(
        [
            {
                "guarded": {
                    "try": [
                        {
                            "boom": {
                                "raise": {
                                    "error": {
                                        "type": "https://demo.test/boom",
                                        "status": 503,
                                        "detail": "temporary",
                                    }
                                }
                            }
                        }
                    ],
                    "catch": {
                        "errors": {"with": {"detail": "temporary"}},
                        "do": [{"fallback": {"set": {"used_fallback": '"yes"'}}}],
                    },
                }
            }
        ]
    )

    events = await run_workflow(document)

    assert any(
        event.actions and event.actions.state_delta.get("used_fallback") == "yes"
        for event in events
    )


async def test_retry_and_self_heal_budgets_compose() -> None:
    calls: list[int] = []
    diagnoses: list[int] = []

    async def always_fail() -> None:
        calls.append(1)
        raise RuntimeError("still failing")

    async def diagnose(error: Exception, state: dict) -> dict:
        del error, state
        diagnoses.append(1)
        return {"retry": True}

    document = _document(
        [
            {
                "guarded": {
                    "try": [{"boom": {"call": "always_fail"}}],
                    "catch": {
                        "retry": {"limit": {"attempt": {"count": 1}}},
                        "do": [{"fallback": {"set": {"used_fallback": '"yes"'}}}],
                    },
                    "metadata": {"adk": {"self_heal": {"max_attempts": 2}}},
                }
            }
        ]
    )

    events = await run_workflow(
        document,
        function_registry={"always_fail": always_fail},
        self_healer=diagnose,
    )

    assert len(calls) == 2
    assert len(diagnoses) == 1
    assert any(
        event.actions and event.actions.state_delta.get("used_fallback") == "yes"
        for event in events
    )


async def test_exponential_backoff_grows_delay() -> None:
    from openworkflow_adk.tasks.control_flow import _retry_delay

    policy = {"delay": {"milliseconds": 100}, "backoff": {"exponential": {"ratio": 2}}}
    delays = [_retry_delay(policy, attempt) for attempt in range(3)]
    assert delays[0] == pytest.approx(0.1)
    assert delays[1] == pytest.approx(0.2)
    assert delays[2] == pytest.approx(0.4)


async def test_retry_when_expression_blocks_retry() -> None:
    calls: list[int] = []
    document = _document(
        [
            {
                "guarded": {
                    "try": [
                        {
                            "callFlaky": {
                                "call": "flaky",
                                "with": {"value": "never"},
                            }
                        }
                    ],
                    "catch": {
                        "retry": {
                            "delay": {"milliseconds": 1},
                            "limit": {"attempt": {"count": 5}},
                            "when": "${ false }",
                        },
                        "do": [{"fallback": {"set": {"used_fallback": '"yes"'}}}],
                    },
                }
            }
        ]
    )

    started = time.monotonic()
    await run_workflow(document, function_registry={"flaky": _flaky_function(calls, 99)})

    assert len(calls) == 1  # when:false prevented any retry
    assert time.monotonic() - started < 5
