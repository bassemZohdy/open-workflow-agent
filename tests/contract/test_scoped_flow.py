from __future__ import annotations

import asyncio

import pytest
from engine_cases import engine_cases
from open_workflow_agent.errors import WorkflowSemanticError
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


def _workflow(tasks):
    workflow = generate_default_workflow()
    workflow["do"] = tasks
    return workflow


async def _run(services, engine_name, engine_type, tasks, data=None):
    engine = engine_type()
    await engine.initialize(services)
    plan = compile_workflow(_workflow(tasks))
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="flow-user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        result = await engine.invoke(await engine.compile(plan), handle, data)
        references = [
            event.task_reference
            for event in services.events.events
            if event.event_type == "TaskStarted"
        ]
        return result, references
    finally:
        await engine.shutdown()


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("directive", ["exit", "end"])
@pytest.mark.asyncio
async def test_nested_exit_returns_to_parent_and_end_concludes_workflow(
    services, engine_name, engine_type, directive
):
    tasks = [
        {
            "parent": {
                "do": [
                    {"child": {"set": {"child": True}, "then": directive}},
                    {"never": {"call": "llm:1.0.0@default"}},
                ]
            }
        },
        {"sibling": {"set": {"sibling": True}}},
    ]
    result, references = await _run(services, engine_name, engine_type, tasks, {})
    assert result.status == "completed", result.error
    assert result.output == ({"sibling": True} if directive == "exit" else {"child": True})
    assert references == ["/do/0/parent", "/do/0/parent/do/0/child"] + (
        ["/do/1/sibling"] if directive == "exit" else []
    )
    assert services.model.calls == []


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("value", [-1, 0, 1])
@pytest.mark.asyncio
async def test_switch_defers_default_and_preserves_first_match(
    services, engine_name, engine_type, value
):
    tasks = [
        {
            "route": {
                "switch": [
                    {"default": {"then": "fallback"}},
                    {"negative": {"when": "${ . < 0 }", "then": "negative"}},
                    {"positive": {"when": "${ . > 0 }", "then": "positive"}},
                    {"overlap": {"when": "${ . >= 1 }", "then": "fallback"}},
                ]
            }
        },
        {"negative": {"set": {"selected": "negative"}, "then": "end"}},
        {"positive": {"set": {"selected": "positive"}, "then": "end"}},
        {"fallback": {"set": {"selected": "fallback"}, "then": "end"}},
    ]
    result, references = await _run(services, engine_name, engine_type, tasks, value)
    target = "negative" if value < 0 else "positive" if value > 0 else "fallback"
    assert result.status == "completed", result.error
    assert result.output == {"selected": target}
    assert references == [
        "/do/0/route",
        f"/do/{tasks.index(next(t for t in tasks if target in t))}/{target}",
    ]


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_explicit_case_continue_overrides_switch_task_then(
    services, engine_name, engine_type
):
    result, references = await _run(
        services,
        engine_name,
        engine_type,
        [
            {"route": {"switch": [{"selected": {"then": "continue"}}], "then": "end"}},
            {"after": {"set": {"continued": True}}},
        ],
        {},
    )
    assert result.status == "completed", result.error
    assert result.output == {"continued": True}
    assert references == ["/do/0/route", "/do/1/after"]


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("limit", [0, 2, 3])
@pytest.mark.asyncio
async def test_ordered_loop_while_bindings_and_carried_output(
    services, engine_name, engine_type, limit
):
    tasks = [
        {
            "loop": {
                "for": {"each": "number", "at": "position", "in": [1, 2, 3]},
                "while": "${ $position < " + str(limit) + " }",
                "do": [
                    {
                        "step": {
                            "set": {
                                "sum": "${ .sum + $number }",
                                "positions": "${ .positions + [$position] }",
                            }
                        }
                    }
                ],
            }
        }
    ]
    result, references = await _run(
        services, engine_name, engine_type, tasks, {"sum": 0, "positions": []}
    )
    assert result.status == "completed", result.error
    assert result.output == {"sum": sum([1, 2, 3][:limit]), "positions": list(range(limit))}
    assert references == ["/do/0/loop"] + ["/do/0/loop/do/0/step"] * limit


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_backward_transition_can_repeat_beyond_native_default_recursion_limit(
    services, engine_name, engine_type
):
    result, references = await _run(
        services,
        engine_name,
        engine_type,
        [
            {"increment": {"set": {"n": "${ .n + 1 }"}}},
            {
                "repeat": {
                    "switch": [
                        {"again": {"when": "${ .n < 30 }", "then": "increment"}},
                        {"done": {"then": "end"}},
                    ]
                }
            },
        ],
        {"n": 0},
    )
    assert result.status == "completed", result.error
    assert result.output == {"n": 30}
    assert references == ["/do/0/increment", "/do/1/repeat"] * 30


@pytest.mark.parametrize("kind", ["task", "switch", "catch"])
def test_missing_or_cross_scope_transition_fails_admission(kind):
    definition = {"set": {"ok": True}, "then": "outside"}
    if kind == "switch":
        definition = {"switch": [{"case": {"then": "outside"}}]}
    elif kind == "catch":
        definition = {
            "try": [{"attempt": {"set": {"ok": True}}}],
            "catch": {"then": "outside"},
        }
    with pytest.raises(WorkflowSemanticError, match="current scope"):
        compile_workflow(
            _workflow(
                [{"parent": {"do": [{"child": definition}]}}, {"outside": {"set": {"ok": True}}}]
            )
        )


def test_multiple_defaults_fail_admission():
    with pytest.raises(WorkflowSemanticError, match="default"):
        compile_workflow(
            _workflow(
                [{"route": {"switch": [{"first": {"then": "end"}}, {"second": {"then": "end"}}]}}]
            )
        )


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_task_references_escape_json_pointer_tokens(services, engine_name, engine_type):
    tasks = [{name: {"set": {"reference": "${ $task.reference }"}}} for name in ("a/t", "b/t~")]
    result, references = await _run(services, engine_name, engine_type, tasks, {})
    assert result.status == "completed", result.error
    assert result.output == {"reference": "/do/1/b~1t~0"}
    assert references == ["/do/0/a~1t", "/do/1/b~1t~0"]


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("compete", [False, True])
@pytest.mark.asyncio
async def test_end_in_fork_cancels_inflight_sibling_and_concludes_workflow(
    services, engine_name, engine_type, compete
):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def operation(payload):
        if payload["branch"] == "slow":
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        await started.wait()
        return {"finished": True}

    services.model.response = operation
    result, references = await asyncio.wait_for(
        _run(
            services,
            engine_name,
            engine_type,
            [
                {
                    "fork": {
                        "fork": {
                            "compete": compete,
                            "branches": [
                                {"slow": {"call": "llm:1.0.0@default", "with": {"branch": "slow"}}},
                                {
                                    "finish": {
                                        "call": "llm:1.0.0@default",
                                        "with": {"branch": "finish"},
                                        "then": "end",
                                    }
                                },
                            ],
                        }
                    }
                },
                {"never": {"set": {"wrong": True}}},
            ],
            {},
        ),
        5,
    )
    assert result.status == "completed", result.error
    assert result.output == {"finished": True}
    assert cancelled.is_set()
    cancellation_events = [
        event for event in services.events.events if event.event_type == "TaskCancelled"
    ]
    assert [event.task_reference for event in cancellation_events] == [
        "/do/0/fork/fork/branches/0/slow"
    ]
    assert cancellation_events[0].reason == "workflow_end"
    assert services.events.events[-1].event_type == "WorkflowCompleted"
    assert references == [
        "/do/0/fork",
        "/do/0/fork/fork/branches/0/slow",
        "/do/0/fork/fork/branches/1/finish",
    ]


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("value", [None, False, 2, "text", {"a": 1}])
@pytest.mark.asyncio
async def test_loop_rejects_non_array_values_before_body_effects(
    services, engine_name, engine_type, value
):
    result, references = await _run(
        services,
        engine_name,
        engine_type,
        [
            {
                "loop": {
                    "for": {"in": "${ . }"},
                    "do": [{"never": {"call": "llm:1.0.0@default"}}],
                }
            }
        ],
        value,
    )
    assert result.status == "faulted"
    assert result.error["code"] == "workflow_semantic_error"
    assert result.error["details"]["instance"] == "/do/0/loop"
    assert references == ["/do/0/loop"]
    assert services.model.calls == []


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_nested_fork_end_preserves_ending_ancestors_and_cancels_other_branches(
    services, engine_name, engine_type
):
    started = {name: asyncio.Event() for name in ("outer", "inner")}
    cancelled = set()

    async def operation(payload):
        name = payload["branch"]
        if name in started:
            started[name].set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.add(name)
        await asyncio.gather(*(event.wait() for event in started.values()))
        return {"ended": True}

    services.model.response = operation

    def call(name, **extra):
        return {"call": "llm:1.0.0@default", "with": {"branch": name}, **extra}

    result, references = await asyncio.wait_for(
        _run(
            services,
            engine_name,
            engine_type,
            [
                {
                    "outer": {
                        "fork": {
                            "branches": [
                                {"slow": call("outer")},
                                {
                                    "nested": {
                                        "fork": {
                                            "branches": [
                                                {"slow": call("inner")},
                                                {"finish": call("finish", then="end")},
                                            ]
                                        }
                                    }
                                },
                            ]
                        }
                    }
                },
                {"never": {"set": {"wrong": True}}},
            ],
            {},
        ),
        5,
    )
    assert result.status == "completed", result.error
    assert result.output == {"ended": True}
    assert cancelled == {"outer", "inner"}
    assert "/do/1/never" not in references


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("value", [None, False, 0, "", [], {"__owa_native_input_v1__": None}])
@pytest.mark.asyncio
async def test_native_start_preserves_all_json_input_and_output_types(
    services, engine_name, engine_type, value
):
    result, references = await _run(
        services, engine_name, engine_type, [{"identity": {"set": "${ . }"}}], value
    )
    assert result.status == "completed", result.error
    assert result.output == value
    assert references == ["/do/0/identity"]


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_ending_branch_ancestor_validation_failure_is_not_swallowed(
    services, engine_name, engine_type
):
    result, _ = await _run(
        services,
        engine_name,
        engine_type,
        [
            {
                "fork": {
                    "fork": {
                        "branches": [
                            {
                                "parent": {
                                    "do": [{"finish": {"set": {"done": True}, "then": "end"}}],
                                    "output": {"schema": {"document": False}},
                                }
                            },
                            {"other": {"set": {"other": True}}},
                        ]
                    }
                }
            }
        ],
        {},
    )
    assert result.status == "faulted"
    assert result.error["code"] == "workflow_semantic_error"
    assert result.error["details"]["instance"] == "/do/0/fork/fork/branches/0/parent"
    assert services.events.events[-1].event_type == "WorkflowFaulted"


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_loop_while_root_stays_transformed_task_input_as_body_output_changes(
    services, engine_name, engine_type
):
    result, references = await _run(
        services,
        engine_name,
        engine_type,
        [
            {
                "loop": {
                    "input": {"from": "${ .payload }"},
                    "for": {"in": [1, 2, 3]},
                    "while": "${ .guard and $input.guard }",
                    "do": [{"step": {"set": {"n": "${ .n + $item }"}}}],
                }
            }
        ],
        {"payload": {"guard": True, "n": 0}},
    )
    assert result.status == "completed", result.error
    assert result.output == {"n": 6}
    assert references == ["/do/0/loop"] + ["/do/0/loop/do/0/step"] * 3


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("directive", ["exit", "end"])
@pytest.mark.asyncio
async def test_loop_scope_exit_continues_iterations_and_end_stops_them(
    services, engine_name, engine_type, directive
):
    result, references = await _run(
        services,
        engine_name,
        engine_type,
        [
            {
                "loop": {
                    "for": {"in": [1, 2, 3]},
                    "do": [
                        {"step": {"set": {"n": "${ .n + 1 }"}, "then": directive}},
                        {"never": {"call": "llm:1.0.0@default"}},
                    ],
                }
            },
            {"after": {"set": {"n": "${ .n }", "after": True}}},
        ],
        {"n": 0},
    )
    assert result.status == "completed", result.error
    assert result.output == ({"n": 3, "after": True} if directive == "exit" else {"n": 1})
    assert references == ["/do/0/loop"] + ["/do/0/loop/do/0/step"] * (
        3 if directive == "exit" else 1
    ) + (["/do/1/after"] if directive == "exit" else [])
    assert services.model.calls == []
