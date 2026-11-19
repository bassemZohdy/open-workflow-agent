"""Common state-control routing tests; the fake controller never writes checkpoints."""

from __future__ import annotations

import httpx
import pytest
from native_api_test_support import runtime
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.native_state_policy import CAPABILITY
from open_workflow_agent.state_control_api import mount_state_controls, state_control_capabilities


class Controller:
    def __init__(self):
        self.calls = []
        self.checked = 0

    def verify(self, configuration):
        assert configuration is not None
        self.checked += 1

    async def update(self, handle, authority, **request):
        self.calls.append(("update", handle.invocation_id, authority.principal.identity, request))
        return {"request_id": request["request_id"]}

    async def inspect(self, handle, authority):
        self.calls.append(("inspect", handle.invocation_id, authority.principal.identity, {}))
        return {"updates": []}

    async def resolve(self, handle, authority, **request):
        self.calls.append(("resolve", handle.invocation_id, authority.principal.identity, request))
        return {"decision": request["decision"].decision}


def test_control_capabilities_keep_time_travel_separate():
    data = state_control_capabilities()
    assert data["profile"] == CAPABILITY
    assert data["actions"] == ["state.update", "state.inspect", "state.resolve"]
    assert data["replay"] is data["timeTravel"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "inspect", "resolve"])
@pytest.mark.parametrize(
    "case",
    ["success", "other_owner", "anonymous", "unsupported", "missing", "query", "duplicate_key"],
)
async def test_state_controls_validate_owner_capability_and_unique_mutation_key(
    tmp_path, operation, case
):
    with runtime(tmp_path) as fixture:
        services, engine, handle, actors, app, authorize, invocation, _ = fixture
        controller = Controller()
        services.native_state_controller = None if case == "missing" else controller
        engine.extensions = () if case == "unsupported" else (CAPABILITY,)
        mount_state_controls(app, services, engine, authorize, invocation)
        path = f"/v1/invocations/{handle.invocation_id}/state-updates"
        body = {"base_reference": "a" * 64, "task_reference": "/do/0/noop", "values": {"ok": True}}
        if operation == "resolve":
            path += "/" + "b" * 64 + "/resolve"
            body = {
                "request_digest": "c" * 64,
                "decision": "abandon_unknown",
                "acknowledge_unknown": True,
            }
        if case == "query":
            path += "?private_checkpoint=forbidden"
        headers = [("Idempotency-Key", "first")]
        if case == "duplicate_key":
            headers.append(("Idempotency-Key", "second"))
        actor = None if case == "anonymous" else actors["bob" if case == "other_owner" else "alice"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            with authority_scope(actor):
                response = (
                    await client.get(path)
                    if operation == "inspect"
                    else await client.post(path, json=body, headers=headers)
                )
        succeeds = case == "success" or (operation == "inspect" and case == "duplicate_key")
        if succeeds:
            assert response.status_code == 200, response.text
            assert len(controller.calls) == 1 and controller.checked == 2
            kind, invocation_id, principal, request = controller.calls[0]
            assert (kind, invocation_id, principal) == (operation, handle.invocation_id, "alice")
            if operation == "update":
                assert request["values"] == {"ok": True}
                assert len(request["request_id"]) == 64 and request["base_reference"] == "a" * 64
            elif operation == "resolve":
                assert request["decision"].operation_id == "first"
                assert request["decision"].principal == "alice"
                assert request["operation_id"] == "b" * 64 and request["request_digest"] == "c" * 64
        else:
            assert response.status_code == (404 if case in {"other_owner", "anonymous"} else 422), (
                response.text
            )
            assert controller.calls == []
        assert services.model.calls == []
        assert services.invocations.get(handle.invocation_id).status == "running"
