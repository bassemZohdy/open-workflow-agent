"""Lineage-bound state views verify proof and remain original-owner scoped."""

import pytest
from open_workflow_agent.storage import open_storage
from test_state_branch_api import branch_runtime as branch_runtime
from test_state_replay_api import client_for, first_task_reference, wait_terminal
from test_state_replay_api import policy as policy
from test_state_replay_api import replay_config as replay_config
from test_state_replay_api import replay_runtime as replay_runtime
from test_state_replay_api import state_view_timeout as state_view_timeout
from test_state_replay_api import task_count as task_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch_runtime",
    [
        ("sqlite", "read_only"),
        ("sqlite", "reexecute"),
        ("postgresql", "read_only"),
        ("postgresql", "reexecute"),
    ],
    indirect=True,
)
@pytest.mark.parametrize("damage", ["changed", "missing"])
async def test_branch_views_fail_closed_when_native_lineage_proof_is_damaged(
    branch_runtime, damage
):
    services, _, source, _, policy = branch_runtime
    async with client_for(branch_runtime) as alice, client_for(branch_runtime, "bob") as bob:
        history = await alice.get(f"/v1/invocations/{source.invocation_id}/states")
        assert history.status_code == 200, history.text
        reference = next(
            item["reference"]
            for item in history.json()["items"]
            if item.get("root_task_visits") == {"/do/0/first": 1, "/do/1/second": 1}
        )
        response = await alice.post(
            f"/v1/invocations/{source.invocation_id}/state-branches",
            json={
                "reference": reference,
                "task_reference": "/do/1/second",
                "values": {"value": 7},
                "acknowledge_reexecution": policy == "reexecute",
            },
            headers={"Idempotency-Key": "proof-test"},
        )
        assert response.status_code == 202, response.text
        target = response.json()["invocation_id"]
        await wait_terminal(services, target)
        history = await alice.get(f"/v1/invocations/{target}/states")
        assert history.status_code == 200, history.text
        fork = next(item for item in history.json()["items"] if item["data"] == {"value": 7})
        selected = await alice.get(
            f"/v1/invocations/{target}/state", params={"reference": fork["reference"]}
        )
        assert selected.status_code == 200 and selected.json()["items"][0]["data"] == {"value": 7}
        assert (await bob.get(f"/v1/invocations/{target}/state")).status_code == 404
        connection = open_storage(
            services.engine_database_path("langgraph", create_namespace=False), "owa_langgraph"
        )
        try:
            if damage == "changed":
                connection.execute(
                    "UPDATE owa_state_lineage_v1 SET packet_digest=? WHERE target_id=?",
                    ("0" * 64, target),
                )
            else:
                connection.execute("DELETE FROM owa_state_lineage_v1 WHERE target_id=?", (target,))
            connection.commit()
        finally:
            connection.close()
        assert (await alice.get(f"/v1/invocations/{target}/state")).status_code == 409
        assert (await alice.get(f"/v1/invocations/{target}/states")).status_code == 409
        assert (await alice.get(f"/v1/invocations/{source.invocation_id}/state")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("state_view_timeout", [30], indirect=True)
async def test_replay_target_state_views_remain_owner_scoped(replay_runtime, policy):
    services, _, source, _ = replay_runtime
    async with client_for(replay_runtime) as alice, client_for(replay_runtime, "bob") as bob:
        reference = await first_task_reference(alice, source)
        response = await alice.post(
            f"/v1/invocations/{source.invocation_id}/state-replays",
            json={"reference": reference, "acknowledge_reexecution": policy == "reexecute"},
            headers={"Idempotency-Key": "view-replay"},
        )
        assert response.status_code == 202, response.text
        target = response.json()["invocation_id"]
        await wait_terminal(services, target)
        history = await alice.get(f"/v1/invocations/{target}/states")
        assert history.status_code == 200, history.text
        assert history.json()["invocation_id"] == target
        assert history.json()["continuation"] == "not_inspected"
        assert (await bob.get(f"/v1/invocations/{target}/state")).status_code == 404
