"""A running branch must fail closed when its original authority changes."""

import asyncio

import pytest
from test_state_branch_api import branch_runtime as branch_runtime
from test_state_replay_api import client_for, wait_terminal


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch_runtime",
    [
        ("sqlite", "reexecute"),
        ("postgresql", "reexecute"),
    ],
    indirect=True,
)
async def test_revocation_during_branch_fails_closed(branch_runtime):
    services, _, handle, _, _ = branch_runtime
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(_prompt):
        entered.set()
        await release.wait()
        return {"response": "released"}

    endpoint = f"/v1/invocations/{handle.invocation_id}/state-branches"
    async with client_for(branch_runtime) as client:
        history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
        assert history.status_code == 200, history.text
        reference = next(
            item["reference"]
            for item in history.json()["items"]
            if item.get("root_task_visits") == {"/do/0/first": 1, "/do/1/second": 1}
        )
        body = {
            "reference": reference,
            "task_reference": "/do/1/second",
            "values": {"value": 7},
            "acknowledge_reexecution": True,
        }
        calls = len(services.model.calls)
        services.model.response = held
        actions = services.config.execution_authorization.policy.rules[0].actions
        changed = False
        try:
            accepted = await client.post(endpoint, json=body, headers={"Idempotency-Key": "revoke"})
            assert accepted.status_code == 202, accepted.text
            target = accepted.json()["invocation_id"]
            await asyncio.wait_for(entered.wait(), timeout=30)
            actions.append("changed.action")
            changed = True
            release.set()
            finished = await wait_terminal(services, target)
            assert finished.status == "faulted"
            assert finished.error["code"] == "execution_authorization_denied"
            assert finished.error["details"]["reason"] == "execution_policy_changed"
            assert len(services.model.calls) == calls + 1
            assert services.invocations.get(handle.invocation_id).status == "completed"
        finally:
            release.set()
            if changed:
                actions.remove("changed.action")
