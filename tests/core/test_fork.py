from __future__ import annotations

import asyncio
import copy

import pytest
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.forks import ForkDecision
from open_workflow_agent.workflow import WorkflowExecutor, compile_workflow


@pytest.mark.asyncio
async def test_competing_fork_returns_only_the_first_completed_branch(services):
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "competing-fork",
            "version": "1.0.0",
        },
        "do": [
            {
                "race": {
                    "fork": {
                        "compete": True,
                        "branches": [
                            {"red": {"set": {"colors": ["red"]}}},
                            {"green": {"set": {"colors": ["green"]}}},
                        ],
                    }
                }
            }
        ],
    }

    result = await WorkflowExecutor(services.catalog, services=services).execute(
        compile_workflow(workflow), {}
    )

    assert result["colors"] in (["red"], ["green"])


@pytest.mark.asyncio
async def test_ready_scope_completions_do_not_override_the_first_observed_winner(services):
    class ReadyScopes:
        def __init__(self):
            self.second_completed = asyncio.Event()

        async def run_scope(self, executor, tasks, state, *, prefix, indexed):
            if prefix == "/do":
                name, definition = next(iter(tasks[0].items()))
                await executor._run_task_item(name, definition, state, "/do/0/race")
                return
            name, definition = next(iter(tasks[0].items()))
            state.data = copy.deepcopy(definition["set"])
            if name == "first":
                await self.second_completed.wait()
            else:
                # Wake the first scope before returning the second scope. Its
                # completion is queued before the waiting parent can wake, as
                # can happen when native scope callbacks finish in one turn.
                self.second_completed.set()

    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "ready-competitive-fork",
                "version": "1.0.0",
            },
            "do": [
                {
                    "race": {
                        "fork": {
                            "compete": True,
                            "branches": [
                                {"first": {"set": {"winner": "first"}}},
                                {"second": {"set": {"winner": "second"}}},
                            ],
                        }
                    }
                }
            ],
        }
    )
    result = await WorkflowExecutor(services.catalog, services=services).execute(
        plan, {}, scope_executor=ReadyScopes()
    )
    assert result == {"winner": "second"}


def test_replaying_a_winning_error_preserves_common_status_and_operation_identity():
    decision = ForkDecision(
        winner=1,
        status="faulted",
        output=None,
        context={},
        error={"code": "expression_error", "message": "controlled", "details": {}},
        operation_id="winning-operation",
        cancellation_intents=(0,),
        error_status_code=400,
    )
    with pytest.raises(WorkflowExecutionError) as caught:
        WorkflowExecutor._fork_decision_result(decision, None)
    assert caught.value.status_code == 400
    assert caught.value.code == "expression_error"
    assert caught.value._operation_id == "winning-operation"
